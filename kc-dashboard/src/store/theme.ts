import { create } from "zustand";

const STORAGE_KEY = "konaclaw-theme";
type Theme = "light" | "dark";

function getInitial(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {}
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

interface ThemeState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
}

function applyTheme(t: Theme) {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", t);
  }
  try { localStorage.setItem(STORAGE_KEY, t); } catch {}
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: getInitial(),
  setTheme: (t) => { applyTheme(t); set({ theme: t }); },
  toggle: () => {
    const next = get().theme === "light" ? "dark" : "light";
    applyTheme(next);
    set({ theme: next });
  },
}));

// Sync data-theme attribute on module load (before React mounts).
if (typeof document !== "undefined") {
  applyTheme(getInitial());
}
