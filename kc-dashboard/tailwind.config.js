/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg:           "rgb(var(--bg) / <alpha-value>)",
        bgDeep:       "rgb(var(--bg-deep) / <alpha-value>)",
        panel:        "rgb(var(--panel) / <alpha-value>)",
        panel2:       "rgb(var(--panel-2) / <alpha-value>)",
        panel3:       "rgb(var(--panel-3) / <alpha-value>)",
        line:         "rgb(var(--line) / <alpha-value>)",
        lineBright:   "rgb(var(--line-bright) / <alpha-value>)",
        text:         "rgb(var(--text) / <alpha-value>)",
        textStrong:   "rgb(var(--text-strong) / <alpha-value>)",
        muted:        "rgb(var(--muted) / <alpha-value>)",
        muted2:       "rgb(var(--muted-2) / <alpha-value>)",
        accent:       "rgb(var(--accent) / <alpha-value>)",
        accentBright: "rgb(var(--accent-bright) / <alpha-value>)",
        accentDeep:   "rgb(var(--accent-deep) / <alpha-value>)",
        good:         "rgb(var(--ok) / <alpha-value>)",
        warn:         "rgb(var(--warn) / <alpha-value>)",
        bad:          "rgb(var(--bad) / <alpha-value>)",
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', 'system-ui', 'sans-serif'],
        body:    ['Onest', 'system-ui', 'sans-serif'],
        mono:    ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'card-active': '0 0 0 1px rgb(var(--accent)), 0 8px 20px rgb(var(--accent) / 0.10)',
        'glow-accent': '0 0 12px rgb(var(--accent) / 0.35)',
      },
    },
  },
  plugins: [],
};
