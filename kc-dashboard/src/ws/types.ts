export type ApprovalRequest = {
  request_id: string;
  agent: string;
  tool: string;
  arguments: Record<string, unknown>;
  parent_agent?: string | null;
  subagent_id?: string | null;
};
