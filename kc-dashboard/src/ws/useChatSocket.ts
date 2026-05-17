import { useEffect, useRef, useState, useCallback } from "react";
import { getBaseUrl } from "../api/client";

export type ChatUsageEvent = {
  type: "usage";
  input_tokens: number | null;
  output_tokens: number | null;
  ttfb_ms: number;
  generation_ms: number;
  calls: number;
  usage_reported: boolean;
};

export type ChatEvent =
  | { type: "agent_status"; status: string }
  | { type: "token"; delta: string }
  | { type: "reasoning"; delta: string }
  | { type: "tool_call"; call: { id: string; name: string; arguments?: unknown } }
  | { type: "tool_result"; tool_call_id: string; content?: string }
  | ChatUsageEvent
  | { type: "assistant_complete"; content: string }
  | { type: "stopped"; content: string }
  | { type: "error"; message: string }
  | {
      type: "clarify_request";
      request_id: string;
      conversation_id: number;
      agent: string;
      question: string;
      choices: string[];
      timeout_seconds: number;
      started_at: number;
    }
  | {
      type: "todo_event";
      conversation_id: number;
      agent: string;
      action: "added" | "updated" | "deleted" | "cleared_done";
      item?: {
        id: number;
        title?: string;
        notes?: string;
        status?: "open" | "done";
        scope?: "conversation" | "agent";
      };
      deleted_count?: number;
    }
  | {
      type: "subagent_started";
      subagent_id: string;
      parent_conversation_id: string;
      template: string;
      label?: string | null;
      task_preview: string;
    }
  | {
      type: "subagent_tool";
      subagent_id: string;
      parent_conversation_id: string;
      tool: string;
      args_preview?: string;
      result_preview?: string;
      tier?: string;
    }
  | {
      type: "subagent_approval";
      subagent_id: string;
      parent_conversation_id: string;
      approval_id: string;
      tool: string;
      args_preview?: string;
      attributed_to: string;
    }
  | {
      type: "subagent_finished";
      subagent_id: string;
      parent_conversation_id: string;
      status: "ok" | "error" | "timeout" | "stopped" | "interrupted";
      reply_preview: string;
      duration_ms: number;
      tool_calls_used: number;
      error_message?: string | null;
    };

export function useChatSocket(conversationId: number | null) {
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (conversationId == null) return;
    const url = getBaseUrl().replace(/^http/, "ws") + `/ws/chat/${conversationId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (e) => setEvents((prev) => [...prev, JSON.parse(e.data)]);
    return () => { ws.close(); setEvents([]); };
  }, [conversationId]);

  const sendUserMessage = useCallback(
    (contentOrPayload: string | { type: string; [key: string]: unknown }) => {
      const payload =
        typeof contentOrPayload === "string"
          ? { type: "user_message", content: contentOrPayload }
          : contentOrPayload;
      wsRef.current?.send(JSON.stringify(payload));
    },
    [],
  );

  return { events, sendUserMessage };
}
