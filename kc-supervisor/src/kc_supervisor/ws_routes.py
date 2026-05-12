from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
)
from kc_core.stream_frames import (
    TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage,
)
from kc_supervisor.agents import AgentStatus
from kc_supervisor.skill_slash import resolve_slash_command

logger = logging.getLogger(__name__)


def _handle_subagent_stop_frame(deps, inbound: dict) -> None:
    """Route a subagent_stop inbound frame to the runner's stop() method."""
    sid = inbound.get("subagent_id")
    if isinstance(sid, str) and deps.subagent_runner is not None:
        deps.subagent_runner.stop(sid)


def register_ws_routes(app: FastAPI) -> None:

    @app.websocket("/ws/chat/{conversation_id}")
    async def ws_chat(ws: WebSocket, conversation_id: int):
        await ws.accept()
        loop = asyncio.get_running_loop()
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

        # Phase C: subscribe to todo_event frames for this conversation.
        todo_broadcaster = app.state.deps.todo_broadcaster
        todo_unsubscribe = None
        if todo_broadcaster is not None:
            def _forward_todo(event: dict) -> None:
                if event.get("conversation_id") != conversation_id:
                    # Agent-scoped events still carry the conversation_id of
                    # whatever conversation Kona was in when she mutated the
                    # persistent item. For now, only deliver to that
                    # conversation. Cross-conversation propagation of
                    # persistent items can be added later if needed.
                    return
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_json(event), loop)
                except Exception:
                    pass
            todo_unsubscribe = todo_broadcaster.subscribe(_forward_todo)

        # Subagents: subscribe to subagent_* frames for this conversation. The
        # broadcaster fans out every emitted frame; we filter by
        # parent_conversation_id (stringified to match what the runner emits).
        subagent_broadcaster = app.state.deps.subagent_broadcaster
        subagent_unsubscribe = None
        if subagent_broadcaster is not None:
            cid_str = str(conversation_id)
            def _forward_subagent(frame: dict) -> None:
                if frame.get("parent_conversation_id") != cid_str:
                    return
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_json(frame), loop)
                except Exception:
                    pass
            subagent_unsubscribe = subagent_broadcaster.subscribe(_forward_subagent)

        # Phase C: subscribe to clarify_request frames for this conversation.
        clarify_broker = deps.clarify_broker
        clarify_unsubscribe = None
        if clarify_broker is not None:
            def _forward_clarify(frame: dict) -> None:
                if frame.get("conversation_id") != conversation_id:
                    return
                # ws.send_json is async; schedule onto the running loop.
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_json(frame), loop)
                except Exception:
                    pass
            clarify_unsubscribe = clarify_broker.subscribe(_forward_clarify)
            # Re-emit any in-flight clarifies for this conversation (reconnect).
            for frame in clarify_broker.pending_for_conversation(conversation_id):
                await ws.send_json(frame)

        # Replay any buffered subagent frames for this conversation (reconnect).
        if deps.subagent_trace_buffer is not None:
            for frame in deps.subagent_trace_buffer.snapshot(str(conversation_id)):
                await ws.send_json(frame)

        try:
            while True:
                inbound = await ws.receive_json()
                if inbound.get("type") == "clarify_response":
                    if clarify_broker is None:
                        continue  # silently drop — phase C not wired
                    rid = inbound.get("request_id")
                    choice = inbound.get("choice")  # may be None for skip
                    reason = inbound.get("reason", "answered" if choice is not None else "skipped")
                    if isinstance(rid, str):
                        clarify_broker.resolve(rid, choice=choice, reason=reason)
                    continue
                if inbound.get("type") == "subagent_stop":
                    _handle_subagent_stop_frame(deps, inbound)
                    continue
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

                # Slash command resolution: if the message starts with
                # /<known-skill-name>, prepend the loaded skill body to the
                # text the model sees, but persist the user's *original*
                # text so the chat transcript stays clean.
                agent_input = content
                if deps.skill_index is not None:
                    resolved = resolve_slash_command(
                        content, skill_index=deps.skill_index,
                    )
                    if resolved is not None:
                        loaded, instruction = resolved
                        agent_input = loaded

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

                    # Refresh the memory prefix from disk so any updates from
                    # earlier turns (or other agents writing user.md) are
                    # visible to the model on this turn. Also inject today's
                    # date so the model can resolve "today / this weekend /
                    # next week" instead of guessing from training-cutoff
                    # dates.
                    now = datetime.now().astimezone()
                    tz_name = now.strftime("%Z") or "local"
                    tz_offset = now.strftime("%z") or ""
                    date_prefix = (
                        f"[Current local date and time: {now.strftime('%A, %B %-d, %Y at %-I:%M %p')} "
                        f"{tz_name} ({now.strftime('%Y-%m-%dT%H:%M:%S')}{tz_offset}). "
                        f"The user's timezone is {tz_name}. "
                        f"When calling tools that take time arguments (calendar, scheduling, "
                        f"reminders), pass times in the user's LOCAL timezone, not UTC/GMT. "
                        f"Use the current date above for any time-relative phrases like "
                        f"'today', 'tomorrow', 'this weekend'. Do NOT rely on training-data dates.]\n\n"
                    )
                    memory_prefix = (
                        rt.assembled.memory_reader.format_prefix(agent=rt.name)
                        if rt.assembled.memory_reader is not None else ""
                    )
                    rt.assembled.core_agent.system_prompt = (
                        date_prefix + memory_prefix + rt.assembled.base_system_prompt
                    )

                    rt.set_status(AgentStatus.THINKING)
                    try:
                        await ws.send_json({"type": "agent_status", "status": "thinking"})
                    except (WebSocketDisconnect, RuntimeError):
                        pass

                    # Track whether the WS is still receiving us. If the client
                    # closes mid-stream, we keep iterating send_stream so the
                    # model's reply still gets persisted — only skip the sends.
                    ws_alive = True

                    async def _safe_send(payload: dict) -> None:
                        nonlocal ws_alive
                        if not ws_alive:
                            return
                        try:
                            await ws.send_json(payload)
                        except (WebSocketDisconnect, RuntimeError):
                            ws_alive = False

                    # Per-turn aggregator state
                    agg = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "generation_ms": 0.0,
                        "ttfb_ms": None,
                        "calls": 0,
                        "usage_reported": True,
                    }
                    from kc_supervisor.scheduling.context import set_current_context
                    set_current_context({
                        "conversation_id": conversation_id,
                        "channel": "dashboard",
                        "chat_id": f"dashboard:{conversation_id}",
                        "agent": rt.name,
                    })
                    try:
                        async for frame in rt.assembled.core_agent.send_stream(agent_input):
                            if isinstance(frame, TokenDelta):
                                await _safe_send({"type": "token", "delta": frame.content})
                            elif isinstance(frame, ToolCallStart):
                                deps.conversations.append(conversation_id, ToolCallMessage(
                                    tool_call_id=frame.call["id"],
                                    tool_name=frame.call["name"],
                                    arguments=frame.call["arguments"],
                                ))
                                await _safe_send({"type": "tool_call", "call": frame.call})
                            elif isinstance(frame, ToolResult):
                                deps.conversations.append(conversation_id, ToolResultMessage(
                                    tool_call_id=frame.call_id,
                                    content=frame.content,
                                ))
                                await _safe_send({
                                    "type": "tool_result",
                                    "call_id": frame.call_id,
                                    "content": frame.content,
                                })
                            elif isinstance(frame, TurnUsage):
                                if not frame.usage_reported:
                                    agg["usage_reported"] = False
                                if frame.usage_reported:
                                    agg["input_tokens"] += frame.input_tokens
                                    agg["output_tokens"] += frame.output_tokens
                                agg["generation_ms"] += frame.generation_ms
                                if agg["ttfb_ms"] is None:
                                    agg["ttfb_ms"] = frame.ttfb_ms
                                agg["calls"] += 1
                            elif isinstance(frame, Complete):
                                usage_payload = {
                                    "input_tokens": agg["input_tokens"] if agg["usage_reported"] else None,
                                    "output_tokens": agg["output_tokens"] if agg["usage_reported"] else None,
                                    "ttfb_ms": agg["ttfb_ms"] if agg["ttfb_ms"] is not None else 0.0,
                                    "generation_ms": agg["generation_ms"],
                                    "calls": agg["calls"],
                                    "usage_reported": agg["usage_reported"],
                                }
                                if agg["calls"] > 0:
                                    await _safe_send({"type": "usage", **usage_payload})
                                deps.conversations.append(
                                    conversation_id, frame.reply,
                                    usage=(usage_payload if agg["calls"] > 0 else None),
                                )
                                await _safe_send({
                                    "type": "assistant_complete",
                                    "content": frame.reply.content,
                                })
                        rt.last_error = None
                        if not ws_alive:
                            # Client went away mid-reply; the reply was still
                            # persisted, so a refresh will pick it up. Drop
                            # the connection cleanly so the outer loop exits.
                            raise WebSocketDisconnect()
                    except WebSocketDisconnect:
                        raise
                    except Exception as e:
                        logger.exception("ws_chat send_stream raised")
                        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                        rt.last_error = msg
                        rt.set_status(AgentStatus.DEGRADED)
                        try:
                            await ws.send_json({
                                "type": "error",
                                "stage": "model_call",
                                "message": msg,
                            })
                        except Exception:
                            pass
                    finally:
                        if rt.status == AgentStatus.THINKING:
                            rt.set_status(AgentStatus.IDLE)
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            if todo_unsubscribe is not None:
                todo_unsubscribe()
            if clarify_unsubscribe is not None:
                clarify_unsubscribe()
            if subagent_unsubscribe is not None:
                subagent_unsubscribe()

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
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            sub.unsubscribe()

    @app.websocket("/ws/reminders")
    async def ws_reminders(ws: WebSocket):
        await ws.accept()
        deps = app.state.deps
        broadcaster = deps.reminders_broadcaster
        if broadcaster is None:
            await ws.send_json({"type": "error", "message": "broadcaster unavailable"})
            await ws.close()
            return

        import asyncio as _asyncio
        import time as _time
        loop = _asyncio.get_running_loop()

        async def _send(event_type: str, reminder_row: dict) -> None:
            try:
                await ws.send_json({
                    "type": event_type,
                    "reminder": reminder_row,
                    "ts": int(_time.time()),
                })
            except Exception:
                logger.warning("ws_reminders failed to send %s", event_type, exc_info=True)

        sub = broadcaster.subscribe(
            lambda et, row: loop.call_soon_threadsafe(_asyncio.create_task, _send(et, row))
        )

        try:
            # Keep the connection open. We don't expect inbound messages, but a
            # blocking receive lets us notice client disconnects.
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            sub.unsubscribe()
