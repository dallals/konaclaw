import { apiGet, apiPatch, apiPost } from "./client";

export type ConnectorStatus = "not_configured" | "connected" | "unavailable" | "error";

export type ConnectorSummary = {
  name: "telegram" | "imessage" | "gmail" | "calendar" | "zapier";
  status: ConnectorStatus;
  has_token: boolean;
  allowlist_count: number;
  summary: string;
};

export type ConnectorDetail = ConnectorSummary & {
  token_hint?: string;
  allowlist?: string[];
  flags?: { platform_supported?: boolean; oauth?: boolean };
};

export type GoogleOAuthStatus = {
  state: "idle" | "pending" | "connected";
  since: number;
  last_error: string | null;
};

export type Zap = {
  tool: string;
  description: string;
  last_used_ts: number | null;
  call_count: number;
};

export const listConnectors = () =>
  apiGet<{ connectors: ConnectorSummary[] }>("/connectors");

export const getConnector = (name: string) =>
  apiGet<ConnectorDetail>(`/connectors/${name}`);

export const patchConnector = (name: string, body: Record<string, unknown>) =>
  apiPatch<{ ok: boolean }>(`/connectors/${name}`, body);

export const googleConnect = () =>
  apiPost<{ state: string; since: number }>("/connectors/google/connect", {});

export const googleStatus = () =>
  apiGet<GoogleOAuthStatus>("/connectors/google/status");

export const googleDisconnect = () =>
  apiPost<{ ok: boolean }>("/connectors/google/disconnect", {});

export const listZaps = () =>
  apiGet<{ zaps: Zap[] }>("/connectors/zapier/zaps");

export const refreshZaps = () =>
  apiPost<{ ok: boolean; refreshed_at: number }>("/connectors/zapier/refresh", {});
