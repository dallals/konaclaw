import { useApprovals } from "../store/approvals";
import { useWS } from "../ws/WSContext";
import { ApprovalCard } from "../components/ApprovalCard";

export default function Permissions() {
  const { pending, resolveLocal } = useApprovals();
  const { send } = useWS();

  const respond = (id: string, allowed: boolean) => {
    send({ type: "approval_response", request_id: id, allowed, reason: allowed ? null : "user denied" });
    resolveLocal(id);
  };

  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Permissions <span className="text-xs ml-2 px-2 py-0.5 rounded bg-bad/15 text-[#fca5a5]">{pending.length} pending</span></h2>
      {pending.length === 0
        ? <p className="text-muted text-sm">Nothing pending.</p>
        : pending.map((r) => <ApprovalCard key={r.request_id} req={r} onApprove={(id) => respond(id, true)} onDeny={(id) => respond(id, false)} />)
      }
    </div>
  );
}
