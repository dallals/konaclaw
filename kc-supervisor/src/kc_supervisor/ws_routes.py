from __future__ import annotations
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import UserMessage
from kc_supervisor.agents import AgentStatus


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
