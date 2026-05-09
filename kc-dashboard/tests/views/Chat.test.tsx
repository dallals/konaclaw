import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Chat from "../../src/views/Chat";
import { WSProvider } from "../../src/ws/WSContext";

class FakeWS {
  onopen?: () => void; onmessage?: (e: any) => void; onclose?: () => void;
  sent: string[] = [];
  url: string;
  constructor(url: string) {
    this.url = url;
    setTimeout(() => this.onopen?.(), 0);
  }
  send(s: string) { this.sent.push(s); }
  close() { this.onclose?.(); }
  // Used by the test to push a server-sent message
  push(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}
let lastFakeWS: FakeWS | null = null;
vi.stubGlobal("WebSocket", class {
  constructor(url: string) {
    lastFakeWS = new FakeWS(url);
    return lastFakeWS as any;
  }
});

let messagesPayload: { type: string; content?: string }[] = [];
vi.stubGlobal("fetch", vi.fn(async (url: string, init?: any) => {
  if (url.includes("/agents/kc/conversations") && init?.method === "POST")
    return { ok: true, json: async () => ({ conversation_id: 99 }) };
  if (url.endsWith("/agents")) return { ok: true, json: async () => ({ agents: [{ name: "kc", model: "m", status: "idle", last_error: null }] }) };
  if (url.includes("/conversations/99/messages")) return { ok: true, json: async () => ({ messages: messagesPayload }) };
  if (url.includes("/conversations")) return { ok: true, json: async () => ({ conversations: [] }) };
  return { ok: true, json: async () => ({}) };
}));

const wrap = (ui: React.ReactNode) =>
  <QueryClientProvider client={new QueryClient()}>
    <WSProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </WSProvider>
  </QueryClientProvider>;

describe("Chat view", () => {
  beforeEach(() => { messagesPayload = []; });

  it("starting a new conversation opens a WS to /ws/chat/{cid}", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS?.url).toMatch(/\/ws\/chat\/99$/));
  });

  it("submitting input sends user_message and renders assistant reply", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());

    const input = screen.getByPlaceholderText(/reply/i);
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.submit(input.closest("form")!);

    expect(lastFakeWS!.sent[0]).toBe(JSON.stringify({ type: "user_message", content: "hi" }));

    messagesPayload = [
      { type: "UserMessage", content: "hi" },
      { type: "AssistantMessage", content: "Hello back!" },
    ];
    lastFakeWS!.push({ type: "assistant_complete", content: "Hello back!" });
    expect(await screen.findByText(/Hello back!/)).toBeInTheDocument();
    const replies = await screen.findAllByText(/Hello back!/);
    expect(replies).toHaveLength(1);
  });
});
