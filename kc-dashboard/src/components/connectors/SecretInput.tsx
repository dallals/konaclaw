import { useState } from "react";

type Props = {
  label: string;
  hasValue: boolean;
  tokenHint?: string;
  onSave: (value: string) => Promise<unknown> | void;
};

export default function SecretInput({ label, hasValue, tokenHint, onSave }: Props) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const placeholder = hasValue
    ? (tokenHint ? `••••••••${tokenHint}` : "•••••••• (saved)")
    : "paste token...";

  const save = async () => {
    if (!value) return;
    setBusy(true);
    try {
      await onSave(value);
      setValue("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <label className="text-xs uppercase text-muted tracking-wide">{label}</label>
      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 px-3 py-2 rounded bg-bg border border-line font-mono text-sm"
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button
          className="px-3 py-2 rounded bg-accent text-bg text-sm font-semibold disabled:opacity-50"
          disabled={!value || busy}
          onClick={save}
        >Save</button>
      </div>
    </div>
  );
}
