import { Link } from "react-router-dom";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { getConnector, patchConnector } from "../../api/connectors";
import SecretInput from "./SecretInput";

export default function ZapierPanel() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["connectors", "zapier"], queryFn: () => getConnector("zapier"),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => patchConnector("zapier", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connectors"] }),
  });

  return (
    <div className="space-y-6 max-w-xl">
      <header>
        <h2 className="text-lg font-semibold">⚡ Zapier</h2>
        <p className="text-sm text-muted">
          {data?.has_token ? "API key set." : "API key required to enable Zapier MCP tools."}
        </p>
      </header>
      <SecretInput
        label="Zapier API key"
        hasValue={data?.has_token ?? false}
        tokenHint={data?.token_hint}
        onSave={(value) => patch.mutateAsync({ api_key: value })}
      />
      <Link to="/connectors/zapier"
            className="inline-block px-3 py-2 rounded border border-line text-sm hover:border-accent">
        Manage zaps →
      </Link>
    </div>
  );
}
