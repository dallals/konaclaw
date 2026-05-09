import { useTheme } from "../store/theme";

export function ThemeToggle() {
  const theme = useTheme((s) => s.theme);
  const setTheme = useTheme((s) => s.setTheme);
  return (
    <div className="inline-flex items-center bg-bgDeep border border-line overflow-hidden font-mono text-[9.5px] uppercase tracking-[0.14em]">
      <button
        type="button"
        onClick={() => setTheme("light")}
        className={`px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors ${
          theme === "light"
            ? "bg-accent text-bgDeep font-bold"
            : "text-muted2 hover:text-text"
        }`}
        aria-pressed={theme === "light"}
      >
        <span className="text-[11px] leading-none">☀</span> Day
      </button>
      <button
        type="button"
        onClick={() => setTheme("dark")}
        className={`px-3 py-1.5 inline-flex items-center gap-1.5 border-l border-line transition-colors ${
          theme === "dark"
            ? "bg-accent text-bgDeep font-bold"
            : "text-muted2 hover:text-text"
        }`}
        aria-pressed={theme === "dark"}
      >
        <span className="text-[11px] leading-none">☾</span> Night
      </button>
    </div>
  );
}
