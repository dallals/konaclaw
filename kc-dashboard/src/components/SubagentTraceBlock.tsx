import { useState } from "react";

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
        <div className="px-4 py-3 font-mono text-[12px] text-text leading-snug">
          {started.task_preview && (
            <div className="text-muted text-[11px] mb-2 italic">
              → {started.task_preview}
            </div>
          )}
          {tools.map((f, i) => (
            <div key={i} className="py-1 border-t border-line first:border-t-0">
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
              <div className="text-textStrong font-semibold text-[11px] mb-1">Reply</div>
              <div
                className={`whitespace-pre-wrap text-[12px] ${
                  finished.status === "ok" ? "text-text" : "text-warn"
                }`}
              >
                {finished.status === "ok"
                  ? finished.reply_preview
                  : finished.error_message || finished.status}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
