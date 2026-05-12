import type { TemplateRow } from "../api/subagents";

type Props = {
  row: TemplateRow;
  onEdit: () => void;
  onDelete: () => void;
};

export function SubagentTemplateCard({ row, onEdit, onDelete }: Props) {
  const degraded = row.status === "degraded";
  return (
    <div
      className={`border bg-panel p-4 flex flex-col gap-2 ${
        degraded ? "border-warn" : "border-line"
      }`}
      data-testid={`template-card-${row.name}`}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="font-display font-semibold uppercase tracking-[0.12em] text-[13px] text-textStrong">
          {row.name}
        </h3>
        {degraded && (
          <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-warn px-1.5 py-0.5 border border-warn">
            Degraded
          </span>
        )}
      </header>
      <div className="font-mono text-[10.5px] text-muted tracking-[0.04em]">
        model: {row.model} · tools: {row.tool_count} · mcp: {row.mcp_count}
      </div>
      {row.description && (
        <p className="text-[12px] text-text leading-snug">{row.description}</p>
      )}
      {row.last_error && (
        <p className="text-[11px] font-mono text-warn break-all">{row.last_error}</p>
      )}
      <footer className="flex gap-2 mt-1">
        <button
          onClick={onEdit}
          className="px-2.5 py-1 border border-line text-[11px] uppercase tracking-[0.1em] font-mono hover:bg-panel2"
        >
          Edit
        </button>
        <button
          onClick={onDelete}
          className="px-2.5 py-1 border border-line text-warn text-[11px] uppercase tracking-[0.1em] font-mono hover:bg-panel2"
        >
          Delete
        </button>
      </footer>
    </div>
  );
}
