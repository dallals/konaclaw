export function ThinkingIndicator({ label = "thinking" }: { label?: string }) {
  return (
    <div className="grid grid-cols-[90px_1fr] gap-7 py-[22px] items-start relative">
      <span className="absolute top-[22px] left-[90px] -translate-x-1/2 w-2 h-px bg-accent" />

      <div className="text-right pr-3.5 border-r border-line pt-1">
        <span className="inline-block font-mono text-[9px] font-bold uppercase tracking-[0.16em] px-1.5 py-[2px] leading-[1.4] mb-1.5 text-accent border border-accent">
          K
        </span>
        <div className="font-display font-semibold text-[13px] text-text leading-tight [letter-spacing:-0.01em]">
          kona
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:0ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:300ms]" />
        </div>
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">{label}</span>
      </div>
    </div>
  );
}
