import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  googleConnect, googleStatus, googleDisconnect,
} from "../../api/connectors";

export default function GooglePanel({ which }: { which: "gmail" | "calendar" }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["google-oauth-status"],
    queryFn: googleStatus,
    refetchInterval: (q) => (q.state.data?.state === "pending" ? 2000 : false),
  });

  // When state flips to connected, refetch the per-connector summary so
  // the right-panel and left-rail status pills update together.
  useEffect(() => {
    if (status.data?.state === "connected") {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      qc.invalidateQueries({ queryKey: ["connectors", "gmail"] });
      qc.invalidateQueries({ queryKey: ["connectors", "calendar"] });
    }
  }, [status.data?.state, qc]);

  const connect = useMutation({ mutationFn: googleConnect, onSuccess: () => status.refetch() });
  const disconnect = useMutation({ mutationFn: googleDisconnect,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["connectors"] }); status.refetch(); } });

  const heading = which === "gmail" ? "📧 Gmail" : "📅 Calendar";
  const state = status.data?.state ?? "idle";

  return (
    <div className="space-y-4 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">{heading}</h2>
        <p className="text-sm text-muted">
          One Google OAuth covers Gmail + Calendar. Connecting one connects both.
        </p>
      </header>

      {state === "idle" && (
        <button
          onClick={() => connect.mutate()}
          className="px-4 py-2 rounded bg-accent text-bg font-semibold"
          disabled={connect.isPending}
        >Connect with Google</button>
      )}

      {state === "pending" && (
        <div className="p-3 rounded bg-panel border border-line text-sm">
          Waiting for OAuth completion. A browser tab should have opened —
          finish the flow there.
        </div>
      )}

      {state === "connected" && (
        <div className="space-y-3">
          <div className="p-3 rounded bg-panel border border-good text-sm">Connected</div>
          <button
            onClick={() => disconnect.mutate()}
            className="px-3 py-1.5 rounded border border-bad text-bad text-sm"
          >Disconnect</button>
        </div>
      )}

      {status.data?.last_error && (
        <div className="p-3 rounded bg-panel border border-bad text-sm text-bad">
          {status.data.last_error}
        </div>
      )}
    </div>
  );
}
