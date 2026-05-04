# kc-dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React + TypeScript dashboard that consumes kc-supervisor's HTTP + WebSocket APIs. Six views: Chat, Agents, Shares, Permissions, Monitor, Audit. After this is shipped, you have the **first real usable system** — open a browser, chat with KonaClaw, create a subagent, see the audit log, click Undo on a file delete.

**Architecture:** Vite + React 18 + TypeScript single-page app. Tailwind for styling (matches the dashboard mockup tokens). [TanStack Query](https://tanstack.com/query/latest) for HTTP data fetching/caching. A single global `WSContext` manages two long-lived WebSockets — `/ws/chat/{cid}` (per active conversation) and `/ws/approvals` (one connection per session). State for approvals lives in a Zustand store so any view can show the queue badge. The dashboard is a separate process: a small FastAPI server in `kc-dashboard-server/` builds the Vite output and serves it on a different port from kc-supervisor (default `:8766`), which keeps a clean two-process boundary per the umbrella spec.

**Tech Stack:** Vite 5, React 18, TypeScript 5.4+, Tailwind CSS 3.4, TanStack Query v5, Zustand 4, react-router-dom 6, Vitest + React Testing Library, Playwright for the one true end-to-end smoke test. Server side: Python + FastAPI (just for serving the built bundle and proxying nothing — the browser talks to kc-supervisor directly via CORS).

**Repo bootstrap:** Two folders side-by-side in `~/Desktop/claudeCode/SammyClaw/`:
- `kc-dashboard/` — the React app (npm/pnpm project)
- `kc-dashboard-server/` — the tiny FastAPI server that serves the built bundle

---

## File Structure

```
kc-dashboard/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
├── index.html
├── src/
│   ├── main.tsx                  # entry, router, providers
│   ├── App.tsx                   # shell: top bar + tab nav + outlet
│   ├── api/
│   │   ├── client.ts             # fetch wrapper, base URL config
│   │   ├── agents.ts             # listAgents(), createConversation(), ...
│   │   ├── conversations.ts
│   │   ├── audit.ts
│   │   └── health.ts
│   ├── ws/
│   │   ├── WSContext.tsx         # provider managing /ws/approvals connection
│   │   ├── useChatSocket.ts      # hook for /ws/chat/{cid}
│   │   └── types.ts
│   ├── store/
│   │   └── approvals.ts          # Zustand store for pending approvals
│   ├── views/
│   │   ├── Chat.tsx
│   │   ├── Agents.tsx
│   │   ├── Shares.tsx
│   │   ├── Permissions.tsx
│   │   ├── Monitor.tsx
│   │   └── Audit.tsx
│   ├── components/
│   │   ├── TopBar.tsx
│   │   ├── TabNav.tsx
│   │   ├── ApprovalCard.tsx
│   │   ├── ToolCallCard.tsx
│   │   ├── MessageBubble.tsx
│   │   └── StatusPill.tsx
│   └── styles/
│       └── tokens.css            # CSS vars matching the mockup palette
└── tests/
    ├── App.test.tsx
    ├── views/Chat.test.tsx
    ├── views/Agents.test.tsx
    ├── components/ApprovalCard.test.tsx
    ├── api/client.test.ts
    └── e2e/dashboard.spec.ts     # Playwright

kc-dashboard-server/
├── pyproject.toml
└── src/kc_dashboard_server/
    ├── __init__.py
    └── main.py                   # FastAPI serving the built dist/
```

---

## Task 0: Bootstrap kc-dashboard (Vite + React + TS + Tailwind)

**Files:**
- Create: `kc-dashboard/package.json`, `tsconfig.json`, `vite.config.ts`, `tailwind.config.js`, `index.html`
- Create: `kc-dashboard/src/main.tsx`, `App.tsx`, `styles/tokens.css`

- [ ] **Step 1: Scaffold the project**

```bash
mkdir -p ~/Desktop/claudeCode/SammyClaw/kc-dashboard
cd ~/Desktop/claudeCode/SammyClaw/kc-dashboard
git init -b main
npm create vite@latest . -- --template react-ts
# accept overwrites; this writes package.json, tsconfig.*, vite.config.ts, src/main.tsx, index.html
```

- [ ] **Step 2: Install dependencies**

```bash
npm install @tanstack/react-query zustand react-router-dom
npm install -D tailwindcss postcss autoprefixer @testing-library/react @testing-library/jest-dom vitest jsdom @playwright/test
npx tailwindcss init -p
```

- [ ] **Step 3: Update `tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0f1419",
        panel: "#131922",
        panel2: "#1c2530",
        line: "#1f2935",
        text: "#d4dce4",
        muted: "#94a3b8",
        accent: "#06b6d4",
        good: "#22c55e",
        warn: "#fbbf24",
        bad: "#ef4444",
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 4: Replace `src/index.css` with Tailwind base**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

html, body, #root { height: 100%; background: #0f1419; color: #d4dce4; }
body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif; }
```

- [ ] **Step 5: Add a Vitest config inside `vite.config.ts`**

```ts
/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
```

```ts
// src/test-setup.ts
import "@testing-library/jest-dom";
```

- [ ] **Step 6: Add npm scripts**

In `package.json`, ensure scripts include:

```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "test": "vitest run",
  "test:watch": "vitest",
  "e2e": "playwright test"
}
```

- [ ] **Step 7: Replace `src/main.tsx` with router + providers**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

const qc = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />}>
            <Route index element={<Navigate to="/chat" replace />} />
            <Route path="chat" element={<div>Chat (stub)</div>} />
            <Route path="agents" element={<div>Agents (stub)</div>} />
            <Route path="shares" element={<div>Shares (stub)</div>} />
            <Route path="permissions" element={<div>Permissions (stub)</div>} />
            <Route path="monitor" element={<div>Monitor (stub)</div>} />
            <Route path="audit" element={<div>Audit (stub)</div>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
```

- [ ] **Step 8: Replace `src/App.tsx` with shell skeleton**

```tsx
import { NavLink, Outlet } from "react-router-dom";

const tabs = [
  { to: "/chat", label: "Chat" },
  { to: "/agents", label: "Agents" },
  { to: "/shares", label: "Shares" },
  { to: "/permissions", label: "Permissions" },
  { to: "/monitor", label: "Monitor" },
  { to: "/audit", label: "Audit" },
];

export default function App() {
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center justify-between px-5 py-2.5 bg-panel border-b border-line text-sm">
        <div className="flex items-center gap-2.5 font-semibold">
          <div className="w-5 h-5 rounded-md bg-gradient-to-br from-good to-accent grid place-items-center text-bg font-extrabold text-[10px]">K</div>
          KonaClaw
        </div>
        <div className="flex items-center gap-4 text-muted">
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-good shadow-[0_0_8px_#22c55e80]" /> Healthy</span>
        </div>
      </header>
      <nav className="flex bg-panel border-b border-line px-3">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              "px-4 py-2.5 text-sm border-b-2 " +
              (isActive ? "text-text border-accent font-semibold" : "text-muted border-transparent hover:text-text")
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <main className="flex-1 overflow-auto bg-bg">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 9: Verify the dev server boots**

```bash
npm run dev
# visit http://localhost:5173 — see the shell with tabs
```

- [ ] **Step 10: Commit**

```bash
git add .
git commit -m "chore(kc-dashboard): bootstrap Vite + React + TS + Tailwind shell"
```

---

## Task 1: HTTP API Client

**Files:**
- Create: `src/api/client.ts`, `agents.ts`, `conversations.ts`, `audit.ts`, `health.ts`
- Test: `tests/api/client.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tests/api/client.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, setBaseUrl } from "../../src/api/client";

beforeEach(() => {
  setBaseUrl("http://supervisor.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: any) => ({
    ok: true,
    json: async () => ({ url, init }),
  })));
});

describe("api client", () => {
  it("apiGet calls fetch with the configured base URL", async () => {
    const data = await apiGet<any>("/agents");
    expect(data.url).toBe("http://supervisor.test/agents");
  });

  it("apiPost sends JSON body", async () => {
    const data = await apiPost<any>("/agents/x/conversations", { channel: "dashboard" });
    expect(JSON.parse(data.init.body)).toEqual({ channel: "dashboard" });
    expect(data.init.headers["content-type"]).toBe("application/json");
  });

  it("throws on non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500, text: async () => "boom" })));
    await expect(apiGet("/x")).rejects.toThrow(/500/);
  });
});
```

- [ ] **Step 2: Verify it fails**

`npm run test`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement client.ts**

```ts
// src/api/client.ts
let baseUrl = import.meta.env.VITE_KC_SUPERVISOR_URL ?? "http://127.0.0.1:8765";

export function setBaseUrl(url: string) { baseUrl = url; }
export function getBaseUrl() { return baseUrl; }

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${baseUrl}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}
```

- [ ] **Step 4: Implement the per-resource modules**

```ts
// src/api/agents.ts
import { apiGet, apiPost } from "./client";

export type Agent = {
  name: string; model: string; status: string; last_error: string | null;
};

export const listAgents = () => apiGet<{ agents: Agent[] }>("/agents");
export const createConversation = (agent: string, channel = "dashboard") =>
  apiPost<{ conversation_id: number }>(`/agents/${agent}/conversations`, { channel });
```

```ts
// src/api/conversations.ts
import { apiGet } from "./client";

export type Conversation = { id: number; agent: string; channel: string; started_at: number };
export type StoredMessage = { type: string; content?: string; tool_call_id?: string; tool_name?: string };

export const listConversations = (agent?: string) =>
  apiGet<{ conversations: Conversation[] }>(agent ? `/conversations?agent=${agent}` : "/conversations");
export const listMessages = (cid: number) =>
  apiGet<{ messages: StoredMessage[] }>(`/conversations/${cid}/messages`);
```

```ts
// src/api/audit.ts
import { apiGet, apiPost } from "./client";

export type AuditEntry = {
  id: number; ts: number; agent: string; tool: string;
  args_json: string; decision: string; result: string | null; undoable: number;
};

export const listAudit = (agent?: string, limit = 100) =>
  apiGet<{ entries: AuditEntry[] }>(`/audit?${agent ? `agent=${agent}&` : ""}limit=${limit}`);
export const undoAudit = (id: number) => apiPost<{ undone: boolean }>(`/undo/${id}`, {});
```

```ts
// src/api/health.ts
import { apiGet } from "./client";

export type Health = { status: string; uptime_s: number; agents: number };
export const getHealth = () => apiGet<Health>("/health");
```

- [ ] **Step 5: Verify tests pass**

`npm run test`
Expected: PASS — 3 tests green.

- [ ] **Step 6: Commit**

```bash
git add src/api tests/api
git commit -m "feat(kc-dashboard): add HTTP API client modules"
```

---

## Task 2: Approval Store + WebSocket Provider

**Files:**
- Create: `src/store/approvals.ts`
- Create: `src/ws/types.ts`, `src/ws/WSContext.tsx`
- Test: `tests/store/approvals.test.ts`

**Why:** Approvals are global — they need to show as a badge on the Permissions tab no matter which view is active. Zustand store + a context provider that opens `/ws/approvals` once at app load and pumps requests into the store.

- [ ] **Step 1: Write the failing store test**

```ts
// tests/store/approvals.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { useApprovals } from "../../src/store/approvals";

beforeEach(() => useApprovals.setState({ pending: [] }));

describe("approvals store", () => {
  it("addRequest appends", () => {
    useApprovals.getState().addRequest({ request_id: "r1", agent: "kc", tool: "x", arguments: {} });
    expect(useApprovals.getState().pending).toHaveLength(1);
  });

  it("resolve removes by id", () => {
    const s = useApprovals.getState();
    s.addRequest({ request_id: "r1", agent: "kc", tool: "x", arguments: {} });
    s.addRequest({ request_id: "r2", agent: "kc", tool: "y", arguments: {} });
    s.resolveLocal("r1");
    expect(useApprovals.getState().pending.map((p) => p.request_id)).toEqual(["r2"]);
  });
});
```

- [ ] **Step 2: Verify failure**

`npm run test`
Expected: FAIL.

- [ ] **Step 3: Implement the store**

```ts
// src/ws/types.ts
export type ApprovalRequest = {
  request_id: string;
  agent: string;
  tool: string;
  arguments: Record<string, unknown>;
};
```

```ts
// src/store/approvals.ts
import { create } from "zustand";
import type { ApprovalRequest } from "../ws/types";

type State = {
  pending: ApprovalRequest[];
  addRequest: (r: ApprovalRequest) => void;
  resolveLocal: (request_id: string) => void;
};

export const useApprovals = create<State>((set) => ({
  pending: [],
  addRequest: (r) => set((s) => ({ pending: [...s.pending, r] })),
  resolveLocal: (id) => set((s) => ({ pending: s.pending.filter((p) => p.request_id !== id) })),
}));
```

- [ ] **Step 4: Implement the WSContext provider**

```tsx
// src/ws/WSContext.tsx
import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { getBaseUrl } from "../api/client";
import { useApprovals } from "../store/approvals";

type Ctx = {
  send: (msg: unknown) => void;
  connected: boolean;
};
const WSContext = createContext<Ctx | null>(null);

export function WSProvider({ children }: { children: React.ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const { addRequest, resolveLocal } = useApprovals();

  useEffect(() => {
    const url = getBaseUrl().replace(/^http/, "ws") + "/ws/approvals";
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "approval_request") addRequest(msg);
    };
    return () => ws.close();
  }, [addRequest]);

  const send = (msg: unknown) => {
    wsRef.current?.send(JSON.stringify(msg));
  };
  // wrap resolveLocal so callers can do "respond and locally clear"
  (window as any).__kcResolveLocal = resolveLocal; // for the Permissions view

  return <WSContext.Provider value={{ send, connected }}>{children}</WSContext.Provider>;
}

export function useWS() {
  const v = useContext(WSContext);
  if (!v) throw new Error("useWS outside WSProvider");
  return v;
}
```

- [ ] **Step 5: Wire WSProvider into `main.tsx`**

```tsx
// In main.tsx, wrap <BrowserRouter> with <WSProvider>:
import { WSProvider } from "./ws/WSContext";
// ...
<QueryClientProvider client={qc}>
  <WSProvider>
    <BrowserRouter>...</BrowserRouter>
  </WSProvider>
</QueryClientProvider>
```

- [ ] **Step 6: Verify tests pass**

`npm run test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/store src/ws src/main.tsx tests/store
git commit -m "feat(kc-dashboard): add approvals store + /ws/approvals provider"
```

---

## Task 3: Chat View

**Files:**
- Create: `src/views/Chat.tsx`
- Create: `src/ws/useChatSocket.ts`
- Create: `src/components/MessageBubble.tsx`, `ToolCallCard.tsx`
- Test: `tests/views/Chat.test.tsx`

**Why:** Primary view. Left rail of conversations + right pane streaming chat. Per the v1 supervisor design, messages arrive as `assistant_complete` (single text blob), not token streams — so the bubble appears whole. Tool calls aren't surfaced over WS in v1; they come through the audit log if you want to inspect them after.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/views/Chat.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Chat from "../../src/views/Chat";

class FakeWS {
  onopen?: () => void; onmessage?: (e: any) => void; onclose?: () => void;
  sent: string[] = [];
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0); }
  send(s: string) { this.sent.push(s); }
  close() { this.onclose?.(); }
  // Used by the test to push a server-sent message
  push(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}
let lastFakeWS: FakeWS | null = null;
vi.stubGlobal("WebSocket", function (url: string) {
  lastFakeWS = new FakeWS(url);
  return lastFakeWS;
});

vi.stubGlobal("fetch", vi.fn(async (url: string, init?: any) => {
  if (url.endsWith("/agents")) return { ok: true, json: async () => ({ agents: [{ name: "kc", model: "m", status: "idle", last_error: null }] }) };
  if (url.endsWith("/conversations")) return { ok: true, json: async () => ({ conversations: [] }) };
  if (url.includes("/agents/kc/conversations") && init?.method === "POST")
    return { ok: true, json: async () => ({ conversation_id: 99 }) };
  if (url.includes("/conversations/99/messages")) return { ok: true, json: async () => ({ messages: [] }) };
  return { ok: true, json: async () => ({}) };
}));

const wrap = (ui: React.ReactNode) =>
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>;

describe("Chat view", () => {
  it("starting a new conversation opens a WS to /ws/chat/{cid}", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByText(/start new/i));
    await waitFor(() => expect(lastFakeWS?.url).toMatch(/\/ws\/chat\/99$/));
  });

  it("submitting input sends user_message and renders assistant reply", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByText(/start new/i));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());

    const input = screen.getByPlaceholderText(/reply/i);
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.submit(input.closest("form")!);

    expect(lastFakeWS!.sent[0]).toBe(JSON.stringify({ type: "user_message", content: "hi" }));

    lastFakeWS!.push({ type: "assistant_complete", content: "Hello back!" });
    expect(await screen.findByText(/Hello back!/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Verify it fails**

`npm run test`
Expected: FAIL.

- [ ] **Step 3: Implement the chat socket hook**

```ts
// src/ws/useChatSocket.ts
import { useEffect, useRef, useState, useCallback } from "react";
import { getBaseUrl } from "../api/client";

export type ChatEvent =
  | { type: "agent_status"; status: string }
  | { type: "assistant_complete"; content: string }
  | { type: "error"; message: string };

export function useChatSocket(conversationId: number | null) {
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (conversationId == null) return;
    const url = getBaseUrl().replace(/^http/, "ws") + `/ws/chat/${conversationId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (e) => setEvents((prev) => [...prev, JSON.parse(e.data)]);
    return () => { ws.close(); setEvents([]); };
  }, [conversationId]);

  const sendUserMessage = useCallback((content: string) => {
    wsRef.current?.send(JSON.stringify({ type: "user_message", content }));
  }, []);

  return { events, sendUserMessage };
}
```

- [ ] **Step 4: Implement the message bubble component**

```tsx
// src/components/MessageBubble.tsx
type Role = "user" | "assistant";
export function MessageBubble({ role, content }: { role: Role; content: string }) {
  return (
    <div className={`flex gap-2.5 max-w-3xl ${role === "user" ? "" : ""}`}>
      <div className={`w-7 h-7 rounded-md grid place-items-center font-bold text-[11px] text-bg ${
        role === "user" ? "bg-muted" : "bg-gradient-to-br from-good to-accent"
      }`}>
        {role === "user" ? "S" : "K"}
      </div>
      <div className="bg-panel border border-line rounded-lg px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap flex-1">
        {content}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Implement the Chat view**

```tsx
// src/views/Chat.tsx
import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAgents, createConversation } from "../api/agents";
import { listConversations, listMessages } from "../api/conversations";
import { useChatSocket } from "../ws/useChatSocket";
import { MessageBubble } from "../components/MessageBubble";

export default function Chat() {
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [activeConv, setActiveConv] = useState<number | null>(null);
  const qc = useQueryClient();

  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: listAgents });
  const convsQ = useQuery({
    queryKey: ["conversations", activeAgent],
    queryFn: () => listConversations(activeAgent || undefined),
    enabled: !!activeAgent,
  });
  const msgsQ = useQuery({
    queryKey: ["messages", activeConv],
    queryFn: () => listMessages(activeConv!),
    enabled: activeConv != null,
  });

  const newConv = useMutation({
    mutationFn: () => createConversation(activeAgent!),
    onSuccess: ({ conversation_id }) => {
      setActiveConv(conversation_id);
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const { events, sendUserMessage } = useChatSocket(activeConv);
  const [draft, setDraft] = useState("");

  // Combine persisted messages with live WS events for rendering
  const rendered = useMemo(() => {
    const out: { role: "user" | "assistant"; content: string }[] = [];
    for (const m of msgsQ.data?.messages ?? []) {
      if (m.type === "UserMessage") out.push({ role: "user", content: m.content ?? "" });
      else if (m.type === "AssistantMessage") out.push({ role: "assistant", content: m.content ?? "" });
    }
    for (const e of events) {
      if (e.type === "assistant_complete") out.push({ role: "assistant", content: e.content });
    }
    return out;
  }, [msgsQ.data, events]);

  return (
    <div className="grid grid-cols-[260px_1fr] h-full">
      <aside className="bg-[#0c1117] border-r border-line overflow-y-auto">
        <div className="px-4 py-2 text-xs uppercase text-muted tracking-wider">Agents</div>
        {agentsQ.data?.agents.map((a) => (
          <div
            key={a.name}
            onClick={() => { setActiveAgent(a.name); setActiveConv(null); }}
            className={`px-4 py-2 cursor-pointer hover:bg-panel ${activeAgent === a.name ? "bg-panel border-l-2 border-accent" : ""}`}
          >
            <div className="text-sm">{a.name}</div>
            <div className="text-[11px] text-muted">{a.model} · {a.status}</div>
          </div>
        ))}
      </aside>
      <section className="flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-line">
          <div className="text-sm font-semibold">{activeAgent ?? "Pick an agent"}</div>
          {activeAgent && (
            <button
              className="text-xs px-2 py-1 bg-accent text-bg font-bold rounded"
              onClick={() => newConv.mutate()}
            >
              + Start new
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-3.5">
          {rendered.map((m, i) => <MessageBubble key={i} role={m.role} content={m.content} />)}
        </div>
        {activeConv != null && (
          <form
            className="flex gap-2.5 p-3.5 border-t border-line bg-[#0c1117]"
            onSubmit={(e) => {
              e.preventDefault();
              if (!draft.trim()) return;
              sendUserMessage(draft);
              // Optimistically push the user message into the rendered list via React Query cache
              qc.setQueryData(["messages", activeConv], (old: any) => ({
                messages: [...(old?.messages ?? []), { type: "UserMessage", content: draft }],
              }));
              setDraft("");
            }}
          >
            <input
              className="flex-1 bg-panel border border-line rounded-lg px-3.5 py-2.5 text-sm text-text placeholder:text-[#475569]"
              placeholder="Reply…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <button className="bg-accent text-bg px-4 rounded-lg font-bold text-sm">Send</button>
          </form>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 6: Wire the route in `main.tsx`**

Replace `<Route path="chat" element={<div>Chat (stub)</div>} />` with `<Route path="chat" element={<Chat />} />` and `import Chat from "./views/Chat"`.

- [ ] **Step 7: Verify tests pass**

`npm run test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/views/Chat.tsx src/ws/useChatSocket.ts src/components/MessageBubble.tsx src/main.tsx tests/views
git commit -m "feat(kc-dashboard): add Chat view with /ws/chat integration"
```

---

## Task 4: Agents View

**Files:**
- Create: `src/views/Agents.tsx`
- Create: `src/components/StatusPill.tsx`
- Test: `tests/views/Agents.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/views/Agents.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Agents from "../../src/views/Agents";

vi.stubGlobal("fetch", vi.fn(async (url: string) => {
  if (url.endsWith("/agents")) return { ok: true, json: async () => ({ agents: [
    { name: "KonaClaw", model: "qwen2.5:32b", status: "idle", last_error: null },
    { name: "EmailBot", model: "qwen2.5:14b", status: "thinking", last_error: null },
  ] }) };
  return { ok: true, json: async () => ({}) };
}));

describe("Agents view", () => {
  it("renders an agent table", async () => {
    render(<QueryClientProvider client={new QueryClient()}><Agents /></QueryClientProvider>);
    expect(await screen.findByText(/KonaClaw/)).toBeInTheDocument();
    expect(screen.getByText(/EmailBot/)).toBeInTheDocument();
    expect(screen.getByText(/idle/)).toBeInTheDocument();
    expect(screen.getByText(/thinking/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Verify failure**

`npm run test`
Expected: FAIL.

- [ ] **Step 3: Implement components**

```tsx
// src/components/StatusPill.tsx
const palette: Record<string, string> = {
  idle: "bg-good/15 text-good",
  thinking: "bg-warn/15 text-warn",
  paused: "bg-bad/15 text-[#fca5a5]",
  disabled: "bg-line text-muted",
  degraded: "bg-bad/30 text-[#fca5a5]",
};
export function StatusPill({ status }: { status: string }) {
  const cls = palette[status] ?? "bg-line text-muted";
  return <span className={`text-[11px] px-2 py-0.5 rounded font-semibold ${cls}`}>● {status}</span>;
}
```

```tsx
// src/views/Agents.tsx
import { useQuery } from "@tanstack/react-query";
import { listAgents } from "../api/agents";
import { StatusPill } from "../components/StatusPill";

export default function Agents() {
  const q = useQuery({ queryKey: ["agents"], queryFn: listAgents, refetchInterval: 3000 });

  return (
    <div className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold">Agents</h2>
        <button className="bg-accent text-bg px-3 py-1.5 text-xs font-bold rounded">+ New Agent</button>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-muted">
          <tr><th className="text-left py-2">Name</th><th className="text-left">Model</th><th className="text-left">Status</th><th className="text-left">Error</th></tr>
        </thead>
        <tbody>
          {q.data?.agents.map((a) => (
            <tr key={a.name} className="border-t border-line">
              <td className="py-2 font-medium">{a.name}</td>
              <td><code className="text-warn text-xs">{a.model}</code></td>
              <td><StatusPill status={a.status} /></td>
              <td className="text-xs text-muted">{a.last_error ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Wire route + verify tests + commit**

Replace stub in `main.tsx` with `import Agents from "./views/Agents"` and `<Route path="agents" element={<Agents />} />`.

`npm run test` → PASS.

```bash
git add src/views/Agents.tsx src/components/StatusPill.tsx src/main.tsx tests/views/Agents.test.tsx
git commit -m "feat(kc-dashboard): add Agents view with auto-refresh"
```

---

## Task 5: Permissions View (Approval Queue)

**Files:**
- Create: `src/views/Permissions.tsx`
- Create: `src/components/ApprovalCard.tsx`
- Test: `tests/components/ApprovalCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/ApprovalCard.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ApprovalCard } from "../../src/components/ApprovalCard";

describe("ApprovalCard", () => {
  it("renders the request and fires callbacks", () => {
    const onApprove = vi.fn(); const onDeny = vi.fn();
    render(<ApprovalCard
      req={{ request_id: "r1", agent: "kc", tool: "file.delete", arguments: { share: "r" } }}
      onApprove={onApprove} onDeny={onDeny}
    />);
    expect(screen.getByText(/file.delete/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/approve/i));
    expect(onApprove).toHaveBeenCalledWith("r1");
  });
});
```

- [ ] **Step 2: Verify failure → implement**

```tsx
// src/components/ApprovalCard.tsx
import type { ApprovalRequest } from "../ws/types";

export function ApprovalCard({
  req, onApprove, onDeny,
}: { req: ApprovalRequest; onApprove: (id: string) => void; onDeny: (id: string) => void }) {
  return (
    <div className="bg-bad/10 border border-bad/40 rounded-xl p-3.5 mb-2.5">
      <div className="font-semibold text-[#fca5a5] mb-1.5">⚠ {req.agent} wants <code>{req.tool}</code></div>
      <pre className="bg-black/30 p-2 rounded text-xs text-yellow-200 overflow-auto">{JSON.stringify(req.arguments, null, 2)}</pre>
      <div className="flex gap-2 mt-2.5">
        <button className="bg-bad text-white px-3 py-1.5 rounded font-bold text-xs" onClick={() => onApprove(req.request_id)}>Approve</button>
        <button className="border border-line text-muted px-3 py-1.5 rounded text-xs" onClick={() => onDeny(req.request_id)}>Deny</button>
      </div>
    </div>
  );
}
```

```tsx
// src/views/Permissions.tsx
import { useApprovals } from "../store/approvals";
import { useWS } from "../ws/WSContext";
import { ApprovalCard } from "../components/ApprovalCard";

export default function Permissions() {
  const { pending, resolveLocal } = useApprovals();
  const { send } = useWS();

  const respond = (id: string, allowed: boolean) => {
    send({ type: "approval_response", request_id: id, allowed, reason: allowed ? null : "user denied" });
    resolveLocal(id);
  };

  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Permissions <span className="text-xs ml-2 px-2 py-0.5 rounded bg-bad/15 text-[#fca5a5]">{pending.length} pending</span></h2>
      {pending.length === 0
        ? <p className="text-muted text-sm">Nothing pending.</p>
        : pending.map((r) => <ApprovalCard key={r.request_id} req={r} onApprove={(id) => respond(id, true)} onDeny={(id) => respond(id, false)} />)
      }
    </div>
  );
}
```

- [ ] **Step 3: Wire route + verify + commit**

```bash
git add src/views/Permissions.tsx src/components/ApprovalCard.tsx src/main.tsx tests/components
git commit -m "feat(kc-dashboard): add Permissions view + approval handshake"
```

Update `main.tsx` to import and route Permissions.

---

## Task 6: Audit View

**Files:**
- Create: `src/views/Audit.tsx`
- Test: `tests/views/Audit.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/views/Audit.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Audit from "../../src/views/Audit";

vi.stubGlobal("fetch", vi.fn(async () => ({
  ok: true, json: async () => ({ entries: [
    { id: 1, ts: 1000, agent: "kc", tool: "file.read", args_json: "{}", decision: "safe·auto", result: "ok", undoable: 0 },
    { id: 2, ts: 1001, agent: "kc", tool: "file.delete", args_json: "{}", decision: "destructive·user-approved", result: "ok", undoable: 1 },
  ] })
})));

describe("Audit view", () => {
  it("renders rows with Undo button when undoable", async () => {
    render(<QueryClientProvider client={new QueryClient()}><Audit /></QueryClientProvider>);
    expect(await screen.findByText(/file.read/)).toBeInTheDocument();
    expect(screen.getByText(/file.delete/)).toBeInTheDocument();
    expect(screen.getAllByText(/Undo/i)).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Implement**

```tsx
// src/views/Audit.tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAudit, undoAudit } from "../api/audit";

export default function Audit() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["audit"], queryFn: () => listAudit(), refetchInterval: 3000 });
  const undo = useMutation({
    mutationFn: undoAudit,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["audit"] }),
  });

  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Audit log</h2>
      <table className="w-full text-xs font-mono">
        <thead className="text-muted text-[10px] uppercase">
          <tr><th className="text-left py-2">Time</th><th className="text-left">Agent</th><th className="text-left">Tool</th><th className="text-left">Decision</th><th className="text-left">Result</th><th></th></tr>
        </thead>
        <tbody>
          {q.data?.entries.map((e) => (
            <tr key={e.id} className="border-t border-line">
              <td className="py-2 text-muted">{new Date(e.ts * 1000).toLocaleTimeString()}</td>
              <td className="text-good">{e.agent}</td>
              <td className="text-cyan-300">{e.tool}</td>
              <td>{e.decision}</td>
              <td className="text-text">{e.result ?? "—"}</td>
              <td>
                {e.undoable ? (
                  <button className="text-accent hover:underline" onClick={() => undo.mutate(e.id)}>↩ Undo</button>
                ) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Wire + verify + commit**

```bash
git add src/views/Audit.tsx src/main.tsx tests/views/Audit.test.tsx
git commit -m "feat(kc-dashboard): add Audit view with Undo button"
```

---

## Task 7: Shares & Monitor Views (Read-Only Stubs)

**Files:**
- Create: `src/views/Shares.tsx`, `src/views/Monitor.tsx`

**Why:** These views are listing pages backed by simple GET endpoints. We add minimal functional versions in v1; per-share file browser and the rich Monitor heatmap are v0.2 polish.

- [ ] **Step 1: Implement Shares.tsx**

```tsx
// src/views/Shares.tsx
export default function Shares() {
  return (
    <div className="p-5">
      <h2 className="text-base font-semibold mb-4">Shares</h2>
      <p className="text-muted text-sm">Edit <code>~/KonaClaw/config/shares.yaml</code> and restart the supervisor to add or remove shares. (Live editor is a v0.2 add-on.)</p>
    </div>
  );
}
```

- [ ] **Step 2: Implement Monitor.tsx**

```tsx
// src/views/Monitor.tsx
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/health";

export default function Monitor() {
  const q = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 5000 });
  return (
    <div className="p-5 space-y-4">
      <h2 className="text-base font-semibold">Monitor</h2>
      <div className="grid grid-cols-3 gap-3.5">
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Status</div>
          <div className="text-2xl font-bold text-good">{q.data?.status ?? "…"}</div>
        </div>
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Uptime</div>
          <div className="text-2xl font-bold">{q.data ? `${Math.round(q.data.uptime_s)}s` : "…"}</div>
        </div>
        <div className="bg-panel border border-line rounded-lg p-3.5">
          <div className="text-[11px] uppercase text-muted">Agents</div>
          <div className="text-2xl font-bold">{q.data?.agents ?? "…"}</div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire routes + commit**

Update `main.tsx` to use `Shares` and `Monitor` real components.

```bash
git add src/views/Shares.tsx src/views/Monitor.tsx src/main.tsx
git commit -m "feat(kc-dashboard): add Shares + Monitor stubs (v1)"
```

---

## Task 8: kc-dashboard-server (FastAPI Static Server)

**Files:**
- Create: `kc-dashboard-server/pyproject.toml`, `src/kc_dashboard_server/__init__.py`, `main.py`

- [ ] **Step 1: Create the server**

```bash
mkdir -p ~/Desktop/claudeCode/SammyClaw/kc-dashboard-server/src/kc_dashboard_server
cd ~/Desktop/claudeCode/SammyClaw/kc-dashboard-server
git init -b main
```

```toml
# pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "kc-dashboard-server"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.27"]

[project.scripts]
kc-dashboard-server = "kc_dashboard_server.main:main"

[tool.hatch.build.targets.wheel]
packages = ["src/kc_dashboard_server"]
```

```python
# src/kc_dashboard_server/__init__.py
__version__ = "0.1.0"
```

```python
# src/kc_dashboard_server/main.py
from __future__ import annotations
import os
from pathlib import Path
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


def main() -> None:
    dist = Path(os.environ.get("KC_DASHBOARD_DIST", str(Path(__file__).resolve().parent.parent.parent.parent / "kc-dashboard" / "dist")))
    if not dist.is_dir():
        raise SystemExit(f"dashboard build not found at {dist}. Run `npm run build` in kc-dashboard.")
    app = FastAPI(title="kc-dashboard-server")
    app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(str(dist / "index.html"))

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("KC_DASHBOARD_PORT", "8766")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add .
git commit -m "feat(kc-dashboard-server): tiny FastAPI to serve the built dashboard"
```

---

## Task 9: SMOKE.md and Playwright E2E

**Files:**
- Create: `kc-dashboard/SMOKE.md`
- Create: `kc-dashboard/tests/e2e/dashboard.spec.ts`
- Create: `kc-dashboard/playwright.config.ts`

- [ ] **Step 1: Write the Playwright config**

```ts
// playwright.config.ts
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/e2e",
  use: { baseURL: process.env.KC_DASH_URL ?? "http://127.0.0.1:5173" },
});
```

- [ ] **Step 2: Write the e2e test (skips if supervisor not reachable)**

```ts
// tests/e2e/dashboard.spec.ts
import { test, expect } from "@playwright/test";

test("dashboard shell loads and shows tabs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("KonaClaw")).toBeVisible();
  for (const tab of ["Chat","Agents","Shares","Permissions","Monitor","Audit"]) {
    await expect(page.getByRole("link", { name: tab })).toBeVisible();
  }
});

test("Audit view loads (skips if supervisor not running)", async ({ page, request }) => {
  // Pre-flight: skip cleanly when supervisor is offline
  try { await request.get("http://127.0.0.1:8765/health", { timeout: 1000 }); }
  catch { test.skip(true, "supervisor not reachable"); return; }

  await page.goto("/audit");
  await expect(page.getByText(/Audit log/)).toBeVisible();
});
```

- [ ] **Step 3: Write SMOKE.md**

```markdown
# kc-dashboard — Smoke Checklist

Run on the target machine after `npm install` (in kc-dashboard) and `pip install -e .` (in kc-dashboard-server).

## Dev mode

- [ ] `npm run dev` starts Vite on `http://localhost:5173` — open in a browser.
- [ ] All six tab links visible in the top nav.
- [ ] With kc-supervisor running on `:8765`:
  - [ ] Chat: pick an agent, click "Start new", type "hello", see the assistant reply appear.
  - [ ] Agents: list shows configured agents with status pills.
  - [ ] Audit: shows tool calls; Undo button visible only on undoable rows.
  - [ ] Permissions: trigger a destructive action from another client (e.g., a file.delete via curl POST to a chat WS), see it appear with Approve/Deny.
  - [ ] Monitor: shows uptime + agent count.

## Production build

- [ ] `npm run build` — completes; `dist/` produced.
- [ ] `kc-dashboard-server` boots on `:8766` and the same flow above works against the built bundle.

## Vitest + Playwright

- [ ] `npm run test` — all unit tests green.
- [ ] `npm run e2e` — Playwright runs; tests that require the supervisor skip cleanly when it's not up.
```

- [ ] **Step 4: Commit**

```bash
git add SMOKE.md playwright.config.ts tests/e2e
git commit -m "docs(kc-dashboard): add SMOKE.md and Playwright e2e"
```

---

## Done Criteria

- All Vitest tests green.
- Playwright shell test passes (browser smoke). Supervisor-dependent tests skip cleanly when supervisor is offline.
- Browser end-to-end with running kc-supervisor: chat with KonaClaw, see live agents in Agents view, click Approve on a destructive action and have the agent loop resume, click Undo on an undoable audit row.
- The five remaining sub-projects (kc-mcp, kc-connectors, kc-zapier, kc-memory) only **add** to the Audit and Permissions feeds — the dashboard already renders whatever shows up.
