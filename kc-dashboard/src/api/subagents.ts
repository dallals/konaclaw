import { apiGet, apiPost, apiPatch, apiDelete } from "./client";

export type TemplateRow = {
  name: string;
  description: string;
  model: string;
  tool_count: number;
  mcp_count: number;
  status: "ok" | "degraded";
  last_error: string | null;
};

export type TemplateDetail = {
  name: string;
  yaml: string;
};

export type ActiveSubagent = {
  subagent_id: string;
  template: string;
  label: string | null;
  parent_conversation_id: string;
  tool_calls_used: number;
};

export const listSubagentTemplates = () =>
  apiGet<TemplateRow[]>("/subagent-templates");

export const getSubagentTemplate = (name: string) =>
  apiGet<TemplateDetail>(`/subagent-templates/${encodeURIComponent(name)}`);

export const createSubagentTemplate = (yamlBody: string) =>
  apiPost<{ name: string }>("/subagent-templates", { yaml: yamlBody });

export const updateSubagentTemplate = (name: string, yamlBody: string) =>
  apiPatch<{ name: string }>(`/subagent-templates/${encodeURIComponent(name)}`, {
    yaml: yamlBody,
  });

export const deleteSubagentTemplate = (name: string) =>
  apiDelete(`/subagent-templates/${encodeURIComponent(name)}`);

export const listActiveSubagents = () =>
  apiGet<ActiveSubagent[]>("/subagents/active");

export const stopSubagent = (subagent_id: string) =>
  apiPost<{ stopped: boolean }>(`/subagents/${encodeURIComponent(subagent_id)}/stop`, {});
