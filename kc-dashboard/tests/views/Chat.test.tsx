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

    expect(lastFakeWS!.sent[0]).toBe(
      JSON.stringify({ type: "user_message", content: "hi", think: false }),
    );

    messagesPayload = [
      { type: "UserMessage", content: "hi" },
      { type: "AssistantMessage", content: "Hello back!" },
    ];
    lastFakeWS!.push({ type: "assistant_complete", content: "Hello back!" });
    expect(await screen.findByText(/Hello back!/)).toBeInTheDocument();
    const replies = await screen.findAllByText(/Hello back!/);
    expect(replies).toHaveLength(1);
  });

  it("Think toggle flips the `think` field sent in the next user_message", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());

    // Click the Think pill to enable reasoning for the next message.
    const thinkBtn = screen.getByRole("button", { name: /^think$/i });
    expect(thinkBtn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(thinkBtn);
    expect(thinkBtn.getAttribute("aria-pressed")).toBe("true");

    const input = screen.getByPlaceholderText(/reply/i);
    fireEvent.change(input, { target: { value: "explain quantum" } });
    fireEvent.submit(input.closest("form")!);

    expect(lastFakeWS!.sent[0]).toBe(
      JSON.stringify({ type: "user_message", content: "explain quantum", think: true }),
    );
  });

  it("renders Last reply / TTFB header rows after a usage event", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());

    const input = screen.getByPlaceholderText(/reply/i);
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.submit(input.closest("form")!);

    // Persisted messages payload — the assistant reply WITH usage attached.
    messagesPayload = [
      { type: "UserMessage", content: "hi" },
      {
        type: "AssistantMessage",
        content: "Hello back!",
        usage: {
          input_tokens: 100,
          output_tokens: 412,
          ttfb_ms: 1042,
          generation_ms: 3240,
          calls: 2,
          usage_reported: true,
        },
      },
    ] as any;

    // Stream events ordered as on the wire.
    lastFakeWS!.push({ type: "token", delta: "Hello " });
    lastFakeWS!.push({ type: "token", delta: "back!" });
    lastFakeWS!.push({
      type: "usage",
      input_tokens: 100,
      output_tokens: 412,
      ttfb_ms: 1042,
      generation_ms: 3240,
      calls: 2,
      usage_reported: true,
    });
    lastFakeWS!.push({ type: "assistant_complete", content: "Hello back!" });

    // Header strip: Last reply with authoritative tok/s.
    // 412 / 3.24 ≈ 127 t/s; output count 412 tok.
    expect(await screen.findByText(/127 t\/s/)).toBeInTheDocument();
    // TTFB row.
    expect(screen.getAllByText(/1\.04 s/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 calls/).length).toBeGreaterThan(0);
    // Per-bubble badge appears too (with ttfb prefix).
    expect(screen.getByText(/ttfb 1\.04 s/)).toBeInTheDocument();
  });

  it("Stop button replaces Send while awaiting a reply and sends {type:stop}", async () => {
    render(wrap(<Chat />));
    fireEvent.click(await screen.findByText(/kc/i));
    fireEvent.click(screen.getByRole("button", { name: /new drawing/i }));
    await waitFor(() => expect(lastFakeWS).not.toBeNull());
    const input = await screen.findByPlaceholderText(/reply/i);

    // Pre-submit: Send is visible, Stop is not.
    expect(screen.getByRole("button", { name: /^send\b/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop model/i })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "do a long thing" } });
    fireEvent.submit(input.closest("form")!);

    // While the model is "thinking" (awaitingReply true), Send disappears
    // and Stop appears in its place.
    const stopBtn = await screen.findByRole("button", { name: /stop model/i });
    expect(stopBtn).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^send\b/i })).not.toBeInTheDocument();

    // Clicking Stop sends the stop frame over the same WS.
    fireEvent.click(stopBtn);
    const stopSent = lastFakeWS!.sent.find((s) => JSON.parse(s).type === "stop");
    expect(stopSent).toBe(JSON.stringify({ type: "stop" }));

    // The server replies with a `stopped` frame — that also tells the
    // dashboard the turn ended. Send returns, Stop goes away.
    messagesPayload = [
      { type: "UserMessage", content: "do a long thing" },
      { type: "AssistantMessage", content: "partial reply_[stopped]_" },
    ];
    lastFakeWS!.push({ type: "stopped", content: "partial reply_[stopped]_" });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /stop model/i })).not.toBeInTheDocument()
    );
    expect(screen.getByRole("button", { name: /^send\b/i })).toBeInTheDocument();
  });
});
