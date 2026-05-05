from __future__ import annotations
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
)
from kc_core.stream_frames import (
    TokenDelta, ToolCallStart, ToolResult, Complete,
)
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

        if rt.assembled is None or rt.status == AgentStatus.DEGRADED:
            err = rt.last_error or "agent not assembled"
            await ws.send_json({
                "type": "error",
                "message": f"agent {rt.name} is degraded: {err}",
            })
            await ws.close()
            return
        if rt.status == AgentStatus.DISABLED:
            await ws.send_json({
                "type": "error",
                "message": f"agent {rt.name} is disabled",
            })
            await ws.close()
            return

        lock = deps.conv_locks.get(conversation_id)

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

                async with lock:
                    # Persist user message FIRST so /conversations/{cid}/messages
                    # shows the user input even if the model call fails.
                    deps.conversations.append(conversation_id, UserMessage(content=content))

                    # Rehydrate kc-core Agent.history from SQLite. send_stream appends
                    # its own UserMessage(content), so we trim a trailing UserMessage
                    # from history before assigning.
                    history = deps.conversations.list_messages(conversation_id)
                    if history and isinstance(history[-1], UserMessage):
                        history = history[:-1]
                    rt.assembled.core_agent.history = list(history)

                    rt.set_status(AgentStatus.THINKING)
                    await ws.send_json({"type": "agent_status", "status": "thinking"})
                    try:
                        async for frame in rt.assembled.core_agent.send_stream(content):
                            if isinstance(frame, TokenDelta):
                                await ws.send_json({"type": "token", "delta": frame.content})
                            elif isinstance(frame, ToolCallStart):
                                await ws.send_json({
                                    "type": "tool_call",
                                    "call": frame.call,
                                })
                                deps.conversations.append(conversation_id, ToolCallMessage(
                                    tool_call_id=frame.call["id"],
                                    tool_name=frame.call["name"],
                                    arguments=frame.call["arguments"],
                                ))
                            elif isinstance(frame, ToolResult):
                                await ws.send_json({
                                    "type": "tool_result",
                                    "call_id": frame.call_id,
                                    "content": frame.content,
                                })
                                deps.conversations.append(conversation_id, ToolResultMessage(
                                    tool_call_id=frame.call_id,
                                    content=frame.content,
                                ))
                            elif isinstance(frame, Complete):
                                deps.conversations.append(conversation_id, frame.reply)
                                await ws.send_json({
                                    "type": "assistant_complete",
                                    "content": frame.reply.content,
                                })
                        rt.last_error = None
                    except Exception as e:
                        logger.exception("ws_chat send_stream raised")
                        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                        rt.last_error = msg
                        rt.set_status(AgentStatus.DEGRADED)
                        await ws.send_json({
                            "type": "error",
                            "stage": "model_call",
                            "message": msg,
                        })
                    finally:
                        if rt.status == AgentStatus.THINKING:
                            rt.set_status(AgentStatus.IDLE)
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
