import { apiGet, apiPost } from "./client";

export type AuditEntry = {
  id: number; ts: number; agent: string; tool: string;
  args_json: string; decision: string; result: string | null;
  undoable: number; undone: number;
};

export type DecisionFilter = "all" | "allowed" | "denied";

export const listAudit = (
  agent?: string, limit = 100, decision: DecisionFilter = "all",
) => {
  const params = new URLSearchParams();
  if (agent) params.set("agent", agent);
  params.set("limit", String(limit));
  if (decision !== "all") params.set("decision", decision);
  return apiGet<{ entries: AuditEntry[] }>(`/audit?${params.toString()}`);
};

export const undoAudit = (id: number) => apiPost<{ undone: boolean }>(`/undo/${id}`, {});
