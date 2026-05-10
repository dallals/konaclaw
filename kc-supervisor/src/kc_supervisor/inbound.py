from __future__ import annotations
import logging
from typing import Optional
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
)
from kc_core.stream_frames import (
    TokenDelta, ToolCallStart, ToolResult, Complete, TurnUsage,
)
from kc_supervisor.agents import AgentRegistry, AgentStatus
from kc_supervisor.conversations import ConversationManager
from kc_supervisor.locks import ConversationLocks

logger = logging.getLogger(__name__)


class InboundRouter:
    """Bridges connector inbound messages to agent turns.

    The connector's start(supervisor) receives an instance of this class as
    its `supervisor` argument; calling `supervisor.handle_inbound(env)` from
    the connector starts (or continues) an agent conversation.

    Per-(channel, chat_id, agent) conversation continuity is persisted in
    the ``connector_conv_map`` SQLite table so that it survives supervisor
    restarts — the next inbound message after a restart reuses the same
    conversation row.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        conversations: ConversationManager,
        conv_locks: ConversationLocks,
        routing_table,                  # kc_connectors.routing.RoutingTable
        connector_registry,             # kc_connectors.base.ConnectorRegistry
    ) -> None:
        self.registry = registry
        self.conversations = conversations
        self.conv_locks = conv_locks
        self.routing_table = routing_table
        self.connector_registry = connector_registry

    async def handle_inbound(self, env) -> None:
        """Run an agent turn for a single inbound MessageEnvelope.

        Resolves agent via routing_table, persists user message, runs
        send_stream, persists each frame, and on Complete sends the assistant
        reply back through the originating connector. Errors are logged and
        swallowed — connectors stay running.
        """
        agent_name = self.routing_table.route(env.channel, env.chat_id)
        try:
            rt = self.registry.get(agent_name)
        except KeyError:
            logger.warning("inbound dropped: unknown agent %s for env %s/%s",
                           agent_name, env.channel, env.chat_id)
            return
        if rt.assembled is None:
            logger.warning("inbound dropped: agent %s degraded: %s",
                           agent_name, rt.last_error)
            return

        storage = self.conversations.s
        cid = storage.get_conv_for_chat(env.channel, env.chat_id, agent_name)
        if cid is None or storage.get_conversation(cid) is None:
            cid = self.conversations.start(agent=agent_name, channel=env.channel)
            storage.set_conversation_title(cid, f"{env.channel}:{env.chat_id}")
            storage.put_conv_for_chat(env.channel, env.chat_id, agent_name, cid)

        lock = self.conv_locks.get(cid)
        async with lock:
            self.conversations.append(cid, UserMessage(content=env.content))

            history = self.conversations.list_messages(cid)
            if history and isinstance(history[-1], UserMessage):
                history = history[:-1]
            rt.assembled.core_agent.history = list(history)

            if rt.assembled.memory_reader is not None:
                prefix = rt.assembled.memory_reader.format_prefix(agent=rt.name)
                rt.assembled.core_agent.system_prompt = (
                    prefix + rt.assembled.base_system_prompt if prefix
                    else rt.assembled.base_system_prompt
                )

            rt.set_status(AgentStatus.THINKING)
            reply_text: Optional[str] = None
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
                "conversation_id": cid,
                "channel": env.channel,
                "chat_id": env.chat_id,
                "agent": rt.name,
            })
            try:
                async for frame in rt.assembled.core_agent.send_stream(env.content):
                    if isinstance(frame, TokenDelta):
                        pass  # not bridged to channels
                    elif isinstance(frame, ToolCallStart):
                        self.conversations.append(cid, ToolCallMessage(
                            tool_call_id=frame.call["id"],
                            tool_name=frame.call["name"],
                            arguments=frame.call["arguments"],
                        ))
                    elif isinstance(frame, ToolResult):
                        self.conversations.append(cid, ToolResultMessage(
                            tool_call_id=frame.call_id,
                            content=frame.content,
                        ))
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
                        self.conversations.append(
                            cid, frame.reply,
                            usage=(usage_payload if agg["calls"] > 0 else None),
                        )
                        reply_text = frame.reply.content
                rt.last_error = None
            except Exception as e:
                logger.exception("InboundRouter.handle_inbound send_stream raised")
                msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                rt.last_error = msg
                rt.set_status(AgentStatus.DEGRADED)
                reply_text = f"(error: {msg})"
            finally:
                if rt.status == AgentStatus.THINKING:
                    rt.set_status(AgentStatus.IDLE)

        if reply_text:
            try:
                connector = self.connector_registry.get(env.channel)
                await connector.send(env.chat_id, reply_text)
            except Exception:
                logger.exception("connector.send failed for %s/%s",
                                 env.channel, env.chat_id)
