import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import SecretInput from "./SecretInput";
import AllowlistEditor from "./AllowlistEditor";

export default function TelegramPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "telegram"], queryFn: () => getConnector("telegram"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("telegram", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      qc.invalidateQueries({ queryKey: ["connectors", "telegram"] });
    },
  });

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">📱 Telegram</h2>
        <p className="text-sm text-muted">Bot for sending/receiving messages on allowlisted chats.</p>
      </header>
      <SecretInput
        label="Bot token"
        hasValue={data?.has_token ?? false}
        tokenHint={data?.token_hint}
        onSave={(value) => patch.mutateAsync({ bot_token: value })}
      />
      <AllowlistEditor
        label="Allowed chat IDs"
        values={data?.allowlist ?? []}
        onChange={(next) => patch.mutateAsync({ allowlist: next })}
      />
    </div>
  );
}
