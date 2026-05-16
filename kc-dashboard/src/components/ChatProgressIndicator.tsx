import React, { useMemo } from "react";


export interface ToolCallState {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
}


export interface ChatProgressIndicatorProps {
  toolCalls: ToolCallState[];
  attachmentFilenames: Record<string, string>;
}


function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}


function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return truncate(url, 40);
  }
}


function labelFor(call: ToolCallState, filenames: Record<string, string>): string {
  const name = call.name;
  const args = call.args || {};

  if (name === "read_attachment") {
    const attId = String(args.attachment_id ?? "");
    const filename = filenames[attId];
    return filename ? `Reading ${filename}…` : "Reading attachment…";
  }
  if (name === "list_attachments") {
    return "Listing attachments…";
  }
  if (name === "web_search") {
    return "Searching the web…";
  }
  if (name === "web_fetch") {
    const url = String(args.url ?? "");
    return url ? `Fetching ${truncate(hostOf(url), 40)}…` : "Fetching…";
  }
  if (name.startsWith("mcp.perplexity")) {
    return "Asking Perplexity…";
  }
  return `Running ${name}…`;
}


function iconFor(name: string): string {
  if (name === "read_attachment" || name === "list_attachments") return "📎";
  if (name === "web_search" || name === "web_fetch") return "🌐";
  if (name.startsWith("mcp.perplexity")) return "🤖";
  return "⚙️";
}


function chipLabel(call: ToolCallState, filenames: Record<string, string>): string {
  const args = call.args || {};
  if (call.name === "read_attachment") {
    const attId = String(args.attachment_id ?? "");
    const filename = filenames[attId] ?? "attachment";
    return `read_attachment(${filename})`;
  }
  if (call.name === "list_attachments") {
    return "list_attachments";
  }
  if (call.name === "web_search") {
    const q = String(args.query ?? "");
    return q ? `web_search("${truncate(q, 30)}")` : "web_search";
  }
  if (call.name === "web_fetch") {
    const url = String(args.url ?? "");
    return url ? `web_fetch(${truncate(hostOf(url), 30)})` : "web_fetch";
  }
  return call.name;
}


function statusIcon(status: ToolCallState["status"]): string {
  if (status === "running") return "⟳";
  if (status === "done") return "✓";
  return "⚠";
}


export function ChatProgressIndicator({
  toolCalls,
  attachmentFilenames,
}: ChatProgressIndicatorProps) {
  const topLabel = useMemo(() => {
    for (let i = toolCalls.length - 1; i >= 0; i--) {
      if (toolCalls[i].status === "running") {
        return labelFor(toolCalls[i], attachmentFilenames);
      }
    }
    return "Thinking…";
  }, [toolCalls, attachmentFilenames]);

  return (
    <div className="grid grid-cols-[90px_1fr] gap-7 py-[22px] items-start relative">
      <span className="absolute top-[22px] left-[90px] -translate-x-1/2 w-2 h-px bg-accent" />

      <div className="text-right pr-3.5 border-r border-line pt-1">
        <span className="inline-block font-mono text-[9px] font-bold uppercase tracking-[0.16em] px-1.5 py-[2px] leading-[1.4] mb-1.5 text-accent border border-accent">
          K
        </span>
        <div className="font-display font-semibold text-[13px] text-text leading-tight [letter-spacing:-0.01em]">
          kona
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:300ms]" />
          </div>
          <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
            {topLabel}
          </span>
        </div>

        {toolCalls.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {toolCalls.map((c) => (
              <span
                key={c.id}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-line bg-accent/5 font-mono text-[10px] text-text"
                data-status={c.status}
                data-testid="progress-chip"
              >
                <span>{iconFor(c.name)}</span>
                <span>{chipLabel(c, attachmentFilenames)}</span>
                <span aria-label={c.status}>{statusIcon(c.status)}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
