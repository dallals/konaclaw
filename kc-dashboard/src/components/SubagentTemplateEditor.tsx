import { useState } from "react";
import { createSubagentTemplate, updateSubagentTemplate } from "../api/subagents";

type Props = {
  mode: "create" | "edit";
  initialYaml?: string;
  initialName?: string;
  onClose: () => void;
  onSaved: () => void;
};

const DEFAULT_TEMPLATE_YAML = `name: my-subagent
description: One-line description.
model: claude-opus-4-7
system_prompt: |
  You are a focused subagent. Describe its mission here.
tools:
  skill_view: {}
timeout_seconds: 300
max_tool_calls: 50
`;

export function SubagentTemplateEditor({
  mode, initialYaml, initialName, onClose, onSaved,
}: Props) {
  const [yamlText, setYamlText] = useState(initialYaml ?? DEFAULT_TEMPLATE_YAML);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      if (mode === "create") await createSubagentTemplate(yamlText);
      else await updateSubagentTemplate(initialName!, yamlText);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-label={mode === "create" ? "Create subagent template" : `Edit ${initialName}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-line w-[min(800px,90vw)] max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-line flex items-center justify-between">
          <h2 className="font-display font-semibold uppercase tracking-[0.18em] text-[12.5px] text-textStrong">
            {mode === "create" ? "New Template" : `Edit · ${initialName}`}
          </h2>
          <button
            onClick={onClose}
            className="text-muted hover:text-text"
            disabled={saving}
          >
            ✕
          </button>
        </header>
        <div className="p-5 flex-1 overflow-auto">
          <textarea
            aria-label="template yaml"
            value={yamlText}
            onChange={(e) => setYamlText(e.target.value)}
            rows={24}
            className="w-full font-mono text-[12px] bg-bgDeep border border-line text-text p-3 resize-y"
          />
          {error && (
            <div role="alert" className="mt-3 text-warn text-[12px] font-mono">
              {error}
            </div>
          )}
        </div>
        <footer className="px-5 py-3 border-t border-line flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={saving}
            className="px-4 py-2 border border-line text-text hover:bg-panel2"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 bg-accent text-bgDeep font-semibold hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
