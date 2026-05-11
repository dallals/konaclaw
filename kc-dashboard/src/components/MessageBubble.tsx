import type React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatTokensPerSecond, formatTokenCount, formatTtfb } from "../lib/formatUsage";

type Role = "user" | "assistant";

const ROLE_LABEL: Record<Role, string> = { user: "U", assistant: "K" };
const ROLE_NAME: Record<Role, string> = { user: "Sammy", assistant: "kona" };

export type BubbleUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  ttfb_ms: number;
  generation_ms: number;
  calls: number;
  usage_reported: boolean;
};

function renderBadge(usage: BubbleUsage): React.ReactNode {
  const callsSuffix = usage.calls > 1 ? ` · ${usage.calls} calls` : "";
  const ttfb = `ttfb ${formatTtfb(usage.ttfb_ms)}`;

  if (!usage.usage_reported) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        — ttfb only · {ttfb}{callsSuffix}
      </div>
    );
  }

  const out = usage.output_tokens ?? 0;
  if (out === 0) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        — · {ttfb}{callsSuffix}
      </div>
    );
  }

  if (usage.generation_ms < 50) {
    return (
      <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
        instant · {formatTokenCount(out)}{callsSuffix} · {ttfb}
      </div>
    );
  }

  const tps = (out * 1000) / usage.generation_ms;
  return (
    <div className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted2 mt-2">
      {formatTokensPerSecond(tps)} · {formatTokenCount(out)}{callsSuffix} · {ttfb}
    </div>
  );
}

export function MessageBubble({
  role,
  content,
  usage,
}: {
  role: Role;
  content: string;
  usage?: BubbleUsage;
}) {
  const isEmpty = !content || !content.trim();
  const isUser = role === "user";
  // Suppress the "(no reply...)" placeholder for intermediate / tool-only turns.
  // Two cases collapse here:
  //  1. Persisted assistant row with usage attached but output_tokens === 0
  //     (a final-turn that genuinely produced no content — e.g. tool-only).
  //  2. Live streaming bubble with no usage attached at all (the streaming
  //     buffer briefly renders with whitespace-only content during a tool
  //     turn before the next assistant_complete fires).
  const isToolOnlyTurn =
    role === "assistant" && (usage === undefined || (usage.output_tokens ?? 0) === 0);
  return (
    <div className="grid grid-cols-[90px_1fr] gap-7 py-[22px] items-start relative">
      {/* dimensional tick at the meta/body seam */}
      <span className="absolute top-[22px] left-[90px] -translate-x-1/2 w-2 h-px bg-accent" />

      <div className="text-right pr-3.5 border-r border-line pt-1">
        <span
          className={`inline-block font-mono text-[9px] font-bold uppercase tracking-[0.16em] px-1.5 py-[2px] leading-[1.4] mb-1.5 ${
            isUser
              ? "bg-accent text-bgDeep"
              : "text-accent border border-accent"
          }`}
        >
          {ROLE_LABEL[role]}
        </span>
        <div className="font-display font-semibold text-[13px] text-text leading-tight [letter-spacing:-0.01em]">
          {ROLE_NAME[role]}
        </div>
      </div>

      <div>
        <div
          className={`font-body text-[16px] leading-[1.6] max-w-[64ch] ${
            isUser ? "font-medium text-textStrong whitespace-pre-wrap" : "text-text"
          }`}
        >
          {isEmpty ? (
            isToolOnlyTurn ? null : (
              <span className="italic text-muted">(no reply — model returned empty content; try rephrasing)</span>
            )
          ) : isUser ? (
            content
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                h1: ({ children }) => <h1 className="font-display font-semibold text-[22px] text-textStrong mt-5 mb-2 first:mt-0">{children}</h1>,
                h2: ({ children }) => <h2 className="font-display font-semibold text-[19px] text-textStrong mt-5 mb-2 first:mt-0">{children}</h2>,
                h3: ({ children }) => <h3 className="font-display font-semibold text-[16px] text-textStrong mt-4 mb-1.5 first:mt-0 uppercase tracking-[0.06em]">{children}</h3>,
                h4: ({ children }) => <h4 className="font-display font-semibold text-[14px] text-textStrong mt-3 mb-1 first:mt-0 uppercase tracking-[0.06em]">{children}</h4>,
                ul: ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1 marker:text-accent">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1 marker:text-accent">{children}</ol>,
                li: ({ children }) => <li className="leading-[1.55]">{children}</li>,
                strong: ({ children }) => <strong className="font-semibold text-textStrong">{children}</strong>,
                em: ({ children }) => <em className="italic">{children}</em>,
                a: ({ href, children }) => (
                  <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2 hover:opacity-80">{children}</a>
                ),
                code: ({ className, children }) => {
                  const isBlock = (className ?? "").includes("language-");
                  if (isBlock) {
                    return <code className={className}>{children}</code>;
                  }
                  return <code className="font-mono text-[14px] px-1 py-[1px] bg-bgDeep border border-line rounded-sm">{children}</code>;
                },
                pre: ({ children }) => (
                  <pre className="font-mono text-[13px] bg-bgDeep border border-line rounded-sm p-3 mb-3 overflow-x-auto">{children}</pre>
                ),
                blockquote: ({ children }) => (
                  <blockquote className="border-l-2 border-accent pl-3 my-3 text-muted italic">{children}</blockquote>
                ),
                hr: () => <hr className="my-4 border-line" />,
                table: ({ children }) => (
                  <div className="my-3 overflow-x-auto">
                    <table className="border-collapse text-[14px]">{children}</table>
                  </div>
                ),
                thead: ({ children }) => <thead className="border-b border-line">{children}</thead>,
                tbody: ({ children }) => <tbody>{children}</tbody>,
                tr: ({ children }) => <tr className="border-b border-line/40 last:border-0">{children}</tr>,
                th: ({ children }) => (
                  <th className="text-left font-mono text-[11px] font-bold uppercase tracking-[0.1em] text-accent px-3 py-1.5 align-top">{children}</th>
                ),
                td: ({ children }) => <td className="px-3 py-1.5 align-top">{children}</td>,
              }}
            >
              {content}
            </ReactMarkdown>
          )}
        </div>
        {role === "assistant" && usage && renderBadge(usage)}
      </div>
    </div>
  );
}
