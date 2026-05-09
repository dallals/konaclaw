import type { ApprovalRequest } from "../ws/types";

export function ApprovalCard({
  req, onApprove, onDeny,
}: { req: ApprovalRequest; onApprove: (id: string) => void; onDeny: (id: string) => void }) {
  return (
    <div className="my-3 ml-[120px] max-w-[64ch] border border-accent bg-panel" style={{ background: "rgb(var(--panel))" }}>
      <div className="bg-accent text-bgDeep px-3.5 py-1.5 font-mono text-[9px] uppercase tracking-[0.18em] font-bold flex items-center justify-between gap-3">
        <span>⚠ Tool call · awaiting approval</span>
        <span className="font-medium tracking-[0.06em]">{req.tool}</span>
      </div>
      <div className="px-4 py-3 font-mono text-[12px] text-text leading-[1.55]">
        <div className="mb-2">
          <span className="text-textStrong font-semibold">{req.agent}</span> wants to call{" "}
          <span className="text-textStrong font-semibold">{req.tool}</span> with:
        </div>
        <pre className="bg-bgDeep border border-line px-3 py-2 text-[11px] leading-relaxed overflow-auto whitespace-pre-wrap">
{JSON.stringify(req.arguments, null, 2)}
        </pre>
        <div className="flex gap-2.5 mt-3">
          <button
            type="button"
            onClick={() => onApprove(req.request_id)}
            className="bg-accent text-bgDeep border border-accent px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.16em] font-bold hover:bg-accentBright transition-colors"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => onDeny(req.request_id)}
            className="bg-transparent text-accent border border-accent px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.16em] font-bold hover:bg-accent hover:text-bgDeep transition-colors"
          >
            Deny
          </button>
        </div>
      </div>
    </div>
  );
}
