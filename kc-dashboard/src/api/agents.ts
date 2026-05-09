import { apiGet, apiPost, apiPatch, apiDelete } from "./client";

export type Agent = {
  name: string; model: string; status: string; last_error: string | null;
};

export type ModelInfo = { name: string };

export const listAgents = () => apiGet<{ agents: Agent[] }>("/agents");
export const listModels = () => apiGet<{ models: ModelInfo[]; error?: string }>("/models");
export const createConversation = (agent: string, channel = "dashboard") =>
  apiPost<{ conversation_id: number }>(`/agents/${agent}/conversations`, { channel });
export const createAgent = (req: { name: string; system_prompt: string; model?: string }) =>
  apiPost<Agent>("/agents", req);
export const updateAgent = (name: string, body: { model?: string; system_prompt?: string }) =>
  apiPatch<Agent>(`/agents/${name}`, body);
export const deleteAgent = (name: string) => apiDelete(`/agents/${name}`);

export const AGENT_NAME_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
