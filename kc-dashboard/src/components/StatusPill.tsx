const palette: Record<string, string> = {
  idle: "bg-good/15 text-good",
  thinking: "bg-warn/15 text-warn",
  paused: "bg-bad/15 text-bad",
  disabled: "bg-line text-muted",
  degraded: "bg-bad/30 text-bad",
};

export function StatusPill({ status }: { status: string }) {
  const cls = palette[status] ?? "bg-line text-muted";
  return (
    <span className={`font-mono text-[10px] px-2 py-0.5 font-semibold uppercase tracking-[0.1em] ${cls}`}>
      ● {status}
    </span>
  );
}
