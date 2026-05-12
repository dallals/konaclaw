import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type StartedFrame = {
  subagent_id: string;
  template: string;
  label?: string | null;
  task_preview: string;
};
type ToolFrame = {
  tool: string;
  args_preview?: string;
  result_preview?: string;
  tier?: string;
};
type FinishedFrame = {
  status: "ok" | "error" | "timeout" | "stopped" | "interrupted";
  reply_preview: string;
  duration_ms: number;
  tool_calls_used: number;
  error_message?: string | null;
};

type Props = {
  started: StartedFrame;
  tools: ToolFrame[];
  finished: FinishedFrame | null;
  onStop?: () => void;
};

const STATUS_ICON: Record<string, string> = {
  ok: "✓",
  error: "⚠",
  timeout: "⏱",
  stopped: "⏹",
  interrupted: "⚡",
};

export function SubagentTraceBlock({ started, tools, finished, onStop }: Props) {
  const [expanded, setExpanded] = useState(true);
  const isDone = !!finished;
  const status = finished?.status;
  const headerLabel = `subagent: ${started.template}${started.label ? ` · ${started.label}` : ""}`;

  return (
    <section
      className="my-3 ml-[120px] max-w-[64ch] border border-line bg-panel"
      aria-label={`Subagent trace ${started.subagent_id}`}
      data-testid={`subagent-trace-${started.subagent_id}`}
    >
      <header
        className="bg-panel2 px-3.5 py-2 font-mono text-[10px] uppercase tracking-[0.14em] font-bold text-textStrong flex items-center justify-between gap-3 cursor-pointer hover:bg-bgDeep"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="flex items-center gap-2">
          <span className="text-accent">{expanded ? "▾" : "▸"}</span>
          <span>{headerLabel}</span>
        </span>
        <span className="flex items-center gap-3 text-muted">
          {isDone ? (
            <span>
              {STATUS_ICON[status!] ?? ""} {status} · {tools.length} tools ·{" "}
              {(finished!.duration_ms / 1000).toFixed(1)}s
            </span>
          ) : (
            <span className="text-accent">running…</span>
          )}
          {!isDone && onStop && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onStop();
              }}
              className="px-2 py-0.5 border border-warn text-warn font-mono text-[9px] hover:bg-warn hover:text-bgDeep"
            >
              ⏹ Stop
            </button>
          )}
        </span>
      </header>
      {expanded && (
        <div className="px-4 py-3 text-[12px] text-text leading-snug">
          {started.task_preview && (
            <div className="font-mono text-muted text-[11px] mb-2 italic">
              → {started.task_preview}
            </div>
          )}
          {tools.map((f, i) => (
            <div key={i} className="py-1 border-t border-line first:border-t-0 font-mono">
              <div className="flex items-center gap-2">
                <code className="text-accent">{f.tool}</code>
                {f.tier && <span className="text-muted text-[10px]">[{f.tier}]</span>}
              </div>
              {f.args_preview && (
                <div className="text-muted text-[11px] truncate">args: {f.args_preview}</div>
              )}
              {f.result_preview && (
                <div className="text-muted text-[11px] truncate">→ {f.result_preview}</div>
              )}
            </div>
          ))}
          {finished && (
            <div className="mt-3 pt-2 border-t border-line">
              <div className="font-mono text-textStrong font-semibold text-[11px] mb-2 uppercase tracking-[0.1em]">Reply</div>
              {finished.status === "ok" ? (
                <div className="text-[13px] text-text leading-[1.55]">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                      h1: ({ children }) => <h1 className="font-display font-semibold text-[16px] text-textStrong mt-3 mb-1.5 first:mt-0">{children}</h1>,
                      h2: ({ children }) => <h2 className="font-display font-semibold text-[15px] text-textStrong mt-3 mb-1.5 first:mt-0">{children}</h2>,
                      h3: ({ children }) => <h3 className="font-display font-semibold text-[13px] text-textStrong mt-2.5 mb-1 first:mt-0 uppercase tracking-[0.06em]">{children}</h3>,
                      h4: ({ children }) => <h4 className="font-display font-semibold text-[12px] text-textStrong mt-2 mb-1 first:mt-0 uppercase tracking-[0.06em]">{children}</h4>,
                      ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-0.5 marker:text-accent">{children}</ul>,
                      ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-0.5 marker:text-accent">{children}</ol>,
                      li: ({ children }) => <li className="leading-[1.5]">{children}</li>,
                      strong: ({ children }) => <strong className="font-semibold text-textStrong">{children}</strong>,
                      em: ({ children }) => <em className="italic">{children}</em>,
                      a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2 hover:opacity-80">{children}</a>
                      ),
                      code: ({ className, children }) => {
                        const isBlock = (className ?? "").includes("language-");
                        if (isBlock) return <code className={className}>{children}</code>;
                        return <code className="font-mono text-[12px] px-1 py-[1px] bg-bgDeep border border-line rounded-sm">{children}</code>;
                      },
                      pre: ({ children }) => (
                        <pre className="font-mono text-[12px] bg-bgDeep border border-line rounded-sm p-2.5 mb-2 overflow-x-auto">{children}</pre>
                      ),
                      blockquote: ({ children }) => (
                        <blockquote className="border-l-2 border-accent pl-3 my-2 text-muted italic">{children}</blockquote>
                      ),
                      hr: () => <hr className="my-3 border-line" />,
                      table: ({ children }) => (
                        <div className="my-2 overflow-x-auto">
                          <table className="border-collapse text-[12px]">{children}</table>
                        </div>
                      ),
                      thead: ({ children }) => <thead className="border-b border-line">{children}</thead>,
                      tbody: ({ children }) => <tbody>{children}</tbody>,
                      tr: ({ children }) => <tr className="border-b border-line/40 last:border-0">{children}</tr>,
                      th: ({ children }) => (
                        <th className="text-left font-mono text-[10px] font-bold uppercase tracking-[0.1em] text-accent px-2.5 py-1 align-top">{children}</th>
                      ),
                      td: ({ children }) => <td className="px-2.5 py-1 align-top">{children}</td>,
                    }}
                  >
                    {finished.reply_preview}
                  </ReactMarkdown>
                </div>
              ) : (
                <div className="whitespace-pre-wrap text-[12px] font-mono text-warn">
                  {finished.error_message || finished.status}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
