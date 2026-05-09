import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listAgents, createAgent, deleteAgent, updateAgent, listModels,
  AGENT_NAME_RE, type Agent,
} from "../api/agents";
import { StatusPill } from "../components/StatusPill";

export default function Agents() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agents"], queryFn: listAgents, refetchInterval: 3000 });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: listModels, staleTime: 30_000 });
  const [open, setOpen] = useState(false);

  const del = useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  const setModel = useMutation({
    mutationFn: ({ name, model }: { name: string; model: string }) =>
      updateAgent(name, { model }),
    onMutate: async ({ name, model }) => {
      await qc.cancelQueries({ queryKey: ["agents"] });
      const prev = qc.getQueryData<{ agents: Agent[] }>(["agents"]);
      if (prev) {
        qc.setQueryData<{ agents: Agent[] }>(["agents"], {
          agents: prev.agents.map(a => a.name === name ? { ...a, model } : a),
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["agents"], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  return (
    <div className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold">Agents</h2>
        <button
          onClick={() => setOpen(true)}
          className="bg-accent text-bg px-3 py-1.5 text-xs font-bold rounded"
        >
          + New Agent
        </button>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-muted">
          <tr><th className="text-left py-2">Name</th><th className="text-left">Model</th><th className="text-left">Status</th><th className="text-left">Error</th><th></th></tr>
        </thead>
        <tbody>
          {q.data?.agents.map((a) => (
            <tr key={a.name} className="group border-t border-line">
              <td className="py-2 font-medium">{a.name}</td>
              <td>
                <select
                  aria-label={`model for ${a.name}`}
                  value={a.model}
                  onChange={(e) => setModel.mutate({ name: a.name, model: e.target.value })}
                  disabled={setModel.isPending}
                  className="bg-bg border border-line rounded px-2 py-1 text-xs font-mono text-warn focus:border-accent outline-none"
                >
                  {modelsQ.data?.models.find(m => m.name === a.model) ? null : (
                    <option value={a.model}>{a.model} (not in ollama)</option>
                  )}
                  {modelsQ.data?.models.map(m => (
                    <option key={m.name} value={m.name}>{m.name}</option>
                  ))}
                </select>
              </td>
              <td><StatusPill status={a.status} /></td>
              <td className="text-xs text-muted">{a.last_error ?? "—"}</td>
              <td className="text-right">
                <button
                  onClick={() => {
                    if (confirm(`Delete agent "${a.name}"? Conversations and audit history are kept.`)) {
                      del.mutate(a.name);
                    }
                  }}
                  title="Delete agent"
                  className="text-xs opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-bad px-2"
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-[10px] text-muted mt-3">
        Tool support varies by model. Some local models (e.g. <code>gemma3:4b</code>) don't support function calls and will fail at the first tool call.
      </div>
      {open && (
        <NewAgentModal
          onClose={() => setOpen(false)}
          onCreated={() => qc.invalidateQueries({ queryKey: ["agents"] })}
        />
      )}
    </div>
  );
}

function NewAgentModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: listModels, staleTime: 30_000 });

  const create = useMutation({
    mutationFn: () =>
      createAgent({
        name: name.trim(),
        system_prompt: systemPrompt,
        ...(model.trim() ? { model: model.trim() } : {}),
      }),
    onSuccess: () => {
      onCreated();
      onClose();
    },
    onError: (e: Error) => {
      const m = e.message.match(/→ \d+: (.*)$/s);
      try {
        const detail = m ? JSON.parse(m[1]).detail : e.message;
        setError(typeof detail === "string" ? detail : JSON.stringify(detail));
      } catch {
        setError(e.message);
      }
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!AGENT_NAME_RE.test(name.trim())) {
      setError("Name must start with a letter and contain only letters, digits, _ or - (max 64 chars).");
      return;
    }
    if (!systemPrompt.trim()) {
      setError("System prompt is required.");
      return;
    }
    create.mutate();
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/60 grid place-items-center z-10"
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
        className="bg-panel border border-line rounded-lg p-5 w-[480px] max-w-[90vw] space-y-3"
      >
        <h3 className="text-sm font-semibold">New agent</h3>

        <label className="block">
          <div className="text-[11px] uppercase text-muted mb-1">Name</div>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my-helper"
            className="w-full bg-bg border border-line rounded px-2.5 py-1.5 text-sm outline-none focus:border-accent"
          />
          <div className="text-[10px] text-muted mt-1">
            Letters, digits, _ and -. Must start with a letter.
          </div>
        </label>

        <label className="block">
          <div className="text-[11px] uppercase text-muted mb-1">Model (optional)</div>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full bg-bg border border-line rounded px-2.5 py-1.5 text-sm outline-none focus:border-accent font-mono"
          >
            <option value="">(supervisor default)</option>
            {modelsQ.data?.models.map(m => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </select>
          <div className="text-[10px] text-muted mt-1">
            Any model installed in Ollama. Leave as default to use the supervisor default.
          </div>
        </label>

        <label className="block">
          <div className="text-[11px] uppercase text-muted mb-1">System prompt</div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={6}
            placeholder="You are a helpful assistant..."
            className="w-full bg-bg border border-line rounded px-2.5 py-1.5 text-sm outline-none focus:border-accent resize-y"
          />
        </label>

        {error && (
          <div className="text-xs text-[#fca5a5] bg-bad/10 border border-bad/30 rounded px-2.5 py-1.5">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-3 py-1.5 border border-line rounded text-muted hover:text-text"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={create.isPending}
            className="text-xs px-3 py-1.5 bg-accent text-bg font-bold rounded disabled:opacity-50"
          >
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
