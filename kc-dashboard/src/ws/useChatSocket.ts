import { useEffect, useRef, useState, useCallback } from "react";
import { getBaseUrl } from "../api/client";

export type ChatEvent =
  | { type: "agent_status"; status: string }
  | { type: "token"; delta: string }
  | { type: "tool_call"; call: { id: string; name: string; arguments?: unknown } }
  | { type: "tool_result"; tool_call_id: string; content?: string }
  | { type: "assistant_complete"; content: string }
  | { type: "error"; message: string };

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

  const sendUserMessage = useCallback((content: string) => {
    wsRef.current?.send(JSON.stringify({ type: "user_message", content }));
  }, []);

  return { events, sendUserMessage };
}
