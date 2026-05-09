import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { fetchNews, NewsApiError, type NewsMode, type NewsResponse } from "../api/news";

const LS_MODE = "kc.news.mode";
const LS_VALUE = "kc.news.value";
const LS_COLLAPSED = "kc.news.collapsed";

function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diffMs = Date.now() - t;
  const m = Math.floor(diffMs / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function errorMessage(e: unknown): string {
  if (e instanceof NewsApiError) {
    switch (e.code) {
      case "quota_reached":   return "Daily news quota reached. Try again tomorrow.";
      case "unknown_source":  return "Unknown source. Try: bbc-news, the-verge, reuters, associated-press.";
      case "not_configured":  return "News not configured. Add newsapi_api_key to the supervisor's secrets store.";
      case "missing_param":   return "Enter a topic or source.";
      default:                return "Couldn't reach news service.";
    }
  }
  return "Couldn't reach news service.";
}

export function NewsWidget() {
  const initialMode = (localStorage.getItem(LS_MODE) as NewsMode | null) || "topic";
  const initialValue = localStorage.getItem(LS_VALUE) || "";
  const initialCollapsed = localStorage.getItem(LS_COLLAPSED) === "1";

  const [mode, setMode] = useState<NewsMode>(initialMode);
  const [value, setValue] = useState(initialValue);
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [data, setData] = useState<NewsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { localStorage.setItem(LS_MODE, mode); }, [mode]);
  useEffect(() => { localStorage.setItem(LS_VALUE, value); }, [value]);
  useEffect(() => { localStorage.setItem(LS_COLLAPSED, collapsed ? "1" : "0"); }, [collapsed]);

  const m = useMutation({
    mutationFn: () => fetchNews(mode, value.trim(), 8),
    onSuccess: (r) => { setData(r); setErr(null); },
    onError: (e) => { setData(null); setErr(errorMessage(e)); },
  });

  const onSubmit = () => {
    if (!value.trim()) { setErr("Enter a topic or source."); return; }
    m.mutate();
  };

  if (collapsed) {
    return (
      <aside className="w-10 border-l border-line bg-panel flex items-start justify-center pt-3">
        <button
          aria-label="Expand News"
          onClick={() => setCollapsed(false)}
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted2 hover:text-textStrong"
        >
          ⌃
        </button>
      </aside>
    );
  }

  return (
    <aside className="w-[320px] shrink-0 border-l border-line bg-panel overflow-y-auto">
      <div className="px-4 pt-4 pb-2 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted2 font-medium">
          News
        </span>
        <button
          aria-label="Collapse News"
          onClick={() => setCollapsed(true)}
          className="font-mono text-[10px] text-muted2 hover:text-textStrong"
        >
          ⌃
        </button>
      </div>

      <div className="px-4 flex items-center gap-1 mb-2">
        {(["topic", "source"] as const).map((m_) => (
          <button
            key={m_}
            onClick={() => setMode(m_)}
            className={`px-2 py-1 text-[11px] font-mono uppercase tracking-[0.15em] border ${
              mode === m_ ? "border-accent text-textStrong" : "border-line text-muted2"
            }`}
          >
            {m_ === "topic" ? "Topic" : "Source"}
          </button>
        ))}
      </div>

      <div className="px-4 flex items-center gap-1 mb-3">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={mode === "topic" ? "topic, e.g. climate" : "source slug, e.g. bbc-news"}
          onKeyDown={(e) => { if (e.key === "Enter") onSubmit(); }}
          className="flex-1 bg-bgDeep border border-line rounded-none px-2 py-1 text-[12.5px] text-textStrong outline-none font-body focus:border-accent"
        />
        <button
          onClick={onSubmit}
          disabled={m.isPending}
          className="px-2 py-1 text-[11px] font-mono uppercase tracking-[0.15em] border border-line hover:border-accent disabled:opacity-50"
        >
          {m.isPending ? "…" : "Go"}
        </button>
      </div>

      {err && (
        <div className="mx-4 mb-3 px-2 py-1 border border-line text-[12px] text-muted">
          {err}
        </div>
      )}

      {!err && data && data.articles.length === 0 && (
        <div className="mx-4 mb-3 text-[12px] text-muted italic">
          No articles. Try a broader topic or different source.
        </div>
      )}

      {!err && data && data.articles.length > 0 && (
        <ol className="px-4 pb-6 space-y-3">
          {data.articles.map((a, i) => (
            <li key={`${a.url}-${i}`} className="text-[12.5px] leading-snug">
              <a
                href={a.url}
                target="_blank"
                rel="noreferrer"
                className="text-textStrong hover:text-accent"
              >
                {i + 1}. {a.title}
              </a>
              <div className="font-mono text-[10px] text-muted2 mt-0.5">
                {a.source} · {relativeTime(a.published_at)}
                {data.cached ? " · cached" : ""}
              </div>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
