import { useEffect, useState } from "react";
import {
  listSubagentTemplates,
  getSubagentTemplate,
  deleteSubagentTemplate,
  type TemplateRow,
} from "../api/subagents";
import { SubagentTemplateCard } from "../components/SubagentTemplateCard";
import { SubagentTemplateEditor } from "../components/SubagentTemplateEditor";
import { SubagentActiveRunsPanel } from "../components/SubagentActiveRunsPanel";

type EditorState =
  | { mode: "create" }
  | { mode: "edit"; name: string; yaml: string };

export default function Subagents() {
  const [rows, setRows] = useState<TemplateRow[]>([]);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setRows(await listSubagentTemplates());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function startEdit(name: string) {
    try {
      const { yaml } = await getSubagentTemplate(name);
      setEditor({ mode: "edit", name, yaml });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(name: string) {
    if (!confirm(`Delete template "${name}"?`)) return;
    try {
      await deleteSubagentTemplate(name);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="p-6 max-w-[1200px] mx-auto">
      <header className="flex items-center justify-between mb-6">
        <h1 className="font-display font-bold uppercase tracking-[0.2em] text-[18px] text-textStrong">
          Subagents
        </h1>
        <button
          onClick={() => setEditor({ mode: "create" })}
          className="px-4 py-2 bg-accent text-bgDeep font-semibold hover:opacity-90"
        >
          + New Template
        </button>
      </header>

      {error && (
        <div role="alert" className="mb-4 p-3 border border-warn text-warn text-[12px] font-mono">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-muted font-mono text-[11px] uppercase tracking-[0.1em]">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-muted">
          No subagent templates yet. Click <strong>+ New Template</strong> to create one.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {rows.map((row) => (
            <SubagentTemplateCard
              key={row.name}
              row={row}
              onEdit={() => startEdit(row.name)}
              onDelete={() => handleDelete(row.name)}
            />
          ))}
        </div>
      )}

      <SubagentActiveRunsPanel />

      {editor && (
        <SubagentTemplateEditor
          mode={editor.mode}
          initialName={editor.mode === "edit" ? editor.name : undefined}
          initialYaml={editor.mode === "edit" ? editor.yaml : undefined}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}
