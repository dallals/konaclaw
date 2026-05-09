import { apiGet, apiPatch, apiDelete } from "./client";

export type Conversation = {
  id: number;
  agent: string;
  channel: string;
  started_at: number;
  pinned: number;
  title: string | null;
};
export type StoredMessage = { type: string; content?: string; tool_call_id?: string; tool_name?: string };

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
