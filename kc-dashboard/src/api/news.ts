import { getBaseUrl } from "./client";

export type NewsArticle = {
  title: string;
  source: string;
  url: string;
  published_at: string;
  snippet: string;
};

export type NewsResponse = {
  articles: NewsArticle[];
  cached: boolean;
};

export type NewsErrorCode =
  | "not_configured"
  | "missing_param"
  | "quota_reached"
  | "unknown_source"
  | "upstream_error";

export class NewsApiError extends Error {
  code: NewsErrorCode;
  constructor(code: NewsErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

export type NewsMode = "topic" | "source";

export async function fetchNews(
  mode: NewsMode,
  value: string,
  maxResults = 5,
): Promise<NewsResponse> {
  const params = new URLSearchParams();
  params.set("mode", mode);
  if (mode === "topic") params.set("q", value);
  else params.set("source", value);
  params.set("max_results", String(maxResults));

  const r = await fetch(`${getBaseUrl()}/api/news?${params.toString()}`);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const code = (body?.error ?? "upstream_error") as NewsErrorCode;
    const msg = (body?.message ?? `HTTP ${r.status}`) as string;
    throw new NewsApiError(code, msg);
  }
  return body as NewsResponse;
}
