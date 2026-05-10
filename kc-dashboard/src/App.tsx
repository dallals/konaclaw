import { NavLink, Outlet } from "react-router-dom";
import { useApprovals } from "./store/approvals";
import { ThemeToggle } from "./components/ThemeToggle";

const tabs = [
  { to: "/chat",        label: "Chat",        num: "01" },
  { to: "/agents",      label: "Agents",      num: "02" },
  { to: "/connectors",  label: "Connectors",  num: "03" },
  { to: "/permissions", label: "Permissions", num: "04" },
  { to: "/audit",       label: "Audit",       num: "05" },
  { to: "/monitor",     label: "Monitor",     num: "06" },
  { to: "/reminders",   label: "Reminders",   num: "07" },
  { to: "/skills",      label: "Skills",      num: "08" },
];

export default function App() {
  const pendingCount = useApprovals((s) => s.pending.length);
  return (
    <>
      {/* corner registration marks at the four screen corners */}
      <div className="reg-mark tl" />
      <div className="reg-mark tr" />
      <div className="reg-mark bl" />
      <div className="reg-mark br" />

      <div className="h-full flex flex-col relative z-[2]">
        <header className="flex items-center justify-between px-7 py-3.5 bg-panel border-b border-line relative shadow-[0_4px_16px_rgba(0,0,0,0.08)]">
          <span className="absolute inset-x-0 -bottom-px h-px bg-accent opacity-60" />

          <div className="flex items-center gap-4">
            <div className="relative">
              <div className="w-[38px] h-[38px] rounded-full border-[1.5px] border-accent grid place-items-center bg-bgDeep font-display font-bold text-base text-accent leading-none [letter-spacing:-0.5px]">
                K
              </div>
              <span className="absolute top-1/2 -right-[22px] w-[22px] h-px bg-accent opacity-60" />
            </div>
            <div className="ml-[18px] font-display font-bold text-[18px] tracking-[0.18em] uppercase leading-none text-textStrong">
              Kona<span className="text-accent mx-1 font-normal">/</span>Claw
            </div>
          </div>

          <div className="flex items-center gap-[18px] font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted">
            <ThemeToggle />
            <span className="w-px h-3.5 bg-line" />
            <span><span className="text-muted2">REL</span> <span className="text-text font-medium ml-1">v0.2.1</span></span>
            <span className="w-px h-3.5 bg-line" />
            <span><span className="text-muted2">UPTIME</span> <span className="text-text font-medium ml-1">04:23:11</span></span>
            <span className="w-px h-3.5 bg-line" />
            <span className="inline-flex items-center gap-2 text-text font-semibold">
              <span
                className="w-2 h-2 bg-good"
                style={{
                  boxShadow: "0 0 10px rgb(var(--ok) / 0.7)",
                  animation: "pulse 2.4s ease-in-out infinite",
                }}
              />
              <span>SYS · NOMINAL</span>
            </span>
          </div>
        </header>

        <nav className="flex bg-panel2 border-b border-line px-4">
          {tabs.map((t, i) => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-5 py-3 relative transition-colors ${
                  i > 0 ? "border-l border-line" : ""
                } ` +
                (isActive
                  ? "text-textStrong bg-bg before:content-[''] before:absolute before:top-0 before:inset-x-0 before:h-0.5 before:bg-accent"
                  : "text-muted hover:text-text hover:bg-panel")
              }
            >
              {({ isActive }) => (
                <>
                  <span className={`font-mono text-[10px] tracking-[0.04em] font-medium ${isActive ? "text-accent" : "text-muted2"}`}>
                    {t.num}
                  </span>
                  <span className="font-mono text-[10px] text-muted2">—</span>
                  <span className="font-display font-semibold uppercase text-[12.5px] tracking-[0.18em]">
                    {t.label}
                  </span>
                  {t.to === "/permissions" && pendingCount > 0 && (
                    <span className="ml-2 bg-warn text-bgDeep font-mono text-[9px] font-bold px-1.5 py-0.5 leading-none tracking-[0.05em]">
                      {pendingCount}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <main className="flex-1 overflow-auto bg-bg">
          <Outlet />
        </main>
      </div>

      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
      `}</style>
    </>
  );
}
