from __future__ import annotations
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import UserMessage
from kc_supervisor.agents import AgentStatus

logger = logging.getLogger(__name__)


def register_ws_routes(app: FastAPI) -> None:

    @app.websocket("/ws/chat/{conversation_id}")
    async def ws_chat(ws: WebSocket, conversation_id: int):
        await ws.accept()
        deps = app.state.deps

        conv = deps.storage.get_conversation(conversation_id)
        if conv is None:
            await ws.send_json({
                "type": "error",
                "message": f"unknown conversation {conversation_id}",
            })
            await ws.close()
            return

        try:
            rt = deps.registry.get(conv["agent"])
        except KeyError:
            await ws.send_json({
                "type": "error",
                "message": f"unknown agent {conv['agent']}",
            })
            await ws.close()
            return

        if rt.core_agent is None:
            await ws.send_json({
                "type": "error",
                "message": f"agent {rt.name} not initialized",
            })
            await ws.close()
            return

        try:
            while True:
                inbound = await ws.receive_json()
                if inbound.get("type") != "user_message":
                    await ws.send_json({
                        "type": "error",
                        "message": f"unexpected: {inbound.get('type')}",
                    })
                    continue

                content = inbound.get("content", "")
                if not content:
                    await ws.send_json({
                        "type": "error",
                        "message": "user_message must include non-empty content",
                    })
                    continue
                deps.conversations.append(conversation_id, UserMessage(content=content))
                rt.set_status(AgentStatus.THINKING)
                await ws.send_json({"type": "agent_status", "status": "thinking"})
                try:
                    reply = await rt.core_agent.send(content)
                finally:
                    rt.set_status(AgentStatus.IDLE)
                deps.conversations.append(conversation_id, reply)
                await ws.send_json({
                    "type": "assistant_complete",
                    "content": reply.content,
                })
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/approvals")
    async def ws_approvals(ws: WebSocket):
        await ws.accept()
        deps = app.state.deps

        async def _send(req):
            try:
                await ws.send_json({
                    "type": "approval_request",
                    "request_id": req.request_id,
                    "agent": req.agent,
                    "tool": req.tool,
                    "arguments": req.arguments,
                })
            except Exception:
                logger.warning("ws_approvals failed to send request %s", req.request_id, exc_info=True)

        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        # Subscriber callback runs in whatever thread/loop calls request_approval;
        # schedule the actual send on the WS handler's loop.
        sub = deps.approvals.subscribe(
            lambda req: loop.call_soon_threadsafe(_asyncio.create_task, _send(req))
        )

        try:
            for req in deps.approvals.pending():
                await _send(req)

            while True:
                msg = await ws.receive_json()
                if msg.get("type") != "approval_response":
                    continue
                request_id = msg.get("request_id")
                if not isinstance(request_id, str):
                    logger.warning("ws_approvals received malformed approval_response (no request_id)")
                    continue
                deps.approvals.resolve(
                    request_id=request_id,
                    allowed=bool(msg.get("allowed", False)),
                    reason=msg.get("reason"),
                )
        except WebSocketDisconnect:
            return
        finally:
            sub.unsubscribe()
