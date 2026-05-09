import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import AllowlistEditor from "./AllowlistEditor";

export default function IMessagePanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "imessage"], queryFn: () => getConnector("imessage"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("imessage", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors", "imessage"] }),
  });

  if (data?.flags?.platform_supported === false) {
    return (
      <div className="space-y-3 max-w-xl">
        <h2 className="text-lg font-semibold">💬 iMessage</h2>
        <div className="p-3 rounded bg-panel border border-line text-sm text-muted">
          iMessage requires macOS. This connector is unavailable on the current platform.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">💬 iMessage</h2>
        <p className="text-sm text-muted">macOS Messages.app integration. Requires Full Disk Access.</p>
      </header>
      <AllowlistEditor
        label="Allowed handles"
        values={data?.allowlist ?? []}
        onChange={(next) => patch.mutateAsync({ allowlist: next })}
      />
    </div>
  );
}
