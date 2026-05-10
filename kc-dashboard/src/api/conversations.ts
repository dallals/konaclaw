import { apiGet, apiPatch, apiDelete } from "./client";

export type Conversation = {
  id: number;
  agent: string;
  channel: string;
  started_at: number;
  pinned: number;
  title: string | null;
};
export type StoredMessage = {
  type: string;
  content?: string;
  tool_call_id?: string;
  tool_name?: string;
  usage?: {
    input_tokens: number | null;
    output_tokens: number | null;
    ttfb_ms: number;
    generation_ms: number;
    calls: number;
    usage_reported: boolean;
  };
  // Present on assistant messages that were produced by a fired reminder/cron.
  // Used by the chat bubble to render a "from reminder #N" footer linking back
  // to the Reminders tab (highlights + scrolls to that row).
  scheduled_job_id?: number | null;
};

export const listConversations = (agent?: string) =>
  apiGet<{ conversations: Conversation[] }>(agent ? `/conversations?agent=${agent}` : "/conversations");
export const listMessages = (cid: number) =>
  apiGet<{ messages: StoredMessage[] }>(`/conversations/${cid}/messages`);
export const setConversationPinned = (cid: number, pinned: boolean) =>
  apiPatch<Conversation>(`/conversations/${cid}`, { pinned });
export const setConversationTitle = (cid: number, title: string) =>
  apiPatch<Conversation>(`/conversations/${cid}`, { title });
export const deleteConversation = (cid: number) =>
  apiDelete(`/conversations/${cid}`);
