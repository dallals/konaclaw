import { useEffect, useState } from "react";

export type ClarifyCardProps = {
  request_id: string;
  question: string;
  choices: string[];
  timeout_seconds: number;
  started_at: number;          // seconds since epoch (server-supplied)
  onChoose: (request_id: string, choice: string) => void;
  onSkip: (request_id: string) => void;
  resolved?: { choice: string | null; reason?: string };  // NEW
};

function fmt(secs: number): string {
  if (secs < 0) secs = 0;
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ClarifyCard(props: ClarifyCardProps) {
  const deadline = props.started_at * 1000 + props.timeout_seconds * 1000;
  const [remaining, setRemaining] = useState(() => Math.max(0, deadline - Date.now()) / 1000);

  useEffect(() => {
    const id = setInterval(() => {
      setRemaining(Math.max(0, deadline - Date.now()) / 1000);
    }, 1000);
    return () => clearInterval(id);
  }, [deadline]);

  // Resolved state: render compact history card, no active buttons.
  if (props.resolved !== undefined) {
    const r = props.resolved;
    return (
      <div style={{
        border: "1px solid #555",
        background: "transparent",
        borderRadius: 6,
        padding: "8px 12px",
        margin: "12px 0",
        color: "#888",
        fontSize: 13,
      }}>
        <div style={{ marginBottom: 2 }}>❓ {props.question}</div>
        <div>
          {r.choice !== null
            ? `✓ ${r.choice}`
            : r.reason === "skipped" ? "↷ Skipped" : "⏱ Timed out"}
        </div>
      </div>
    );
  }

  const timedOut = remaining <= 0;

  return (
    <div style={{
      border: "1px solid #d49a3a",
      background: "rgba(212, 154, 58, 0.08)",
      borderRadius: 6,
      padding: 12,
      margin: "12px 0",
    }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        ❓ {props.question}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {props.choices.map((c) => (
          <button
            key={c}
            disabled={timedOut}
            onClick={() => props.onChoose(props.request_id, c)}
            style={{
              padding: "6px 12px",
              border: "1px solid #555",
              borderRadius: 4,
              background: timedOut ? "#222" : "#2a2a2a",
              color: timedOut ? "#666" : "#eee",
              cursor: timedOut ? "default" : "pointer",
            }}
          >
            {c}
          </button>
        ))}
        <button
          disabled={timedOut}
          onClick={() => props.onSkip(props.request_id)}
          style={{
            marginLeft: "auto",
            padding: "6px 12px",
            border: "1px solid #555",
            borderRadius: 4,
            background: "transparent",
            color: "#888",
            cursor: timedOut ? "default" : "pointer",
          }}
        >
          Skip
        </button>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: "#888" }}>
        {timedOut
          ? "⏱ Timed out — Kona moved on"
          : `⏱ Kona is waiting · ${fmt(remaining)} remaining`}
      </div>
    </div>
  );
}
