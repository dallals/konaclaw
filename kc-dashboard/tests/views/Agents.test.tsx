import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Agents from "../../src/views/Agents";

let postedAgent: any = null;
let postShouldFail: { status: number; detail: string } | null = null;
let patchedAgent: { name: string; body: any } | null = null;
let agentsRows = [
  { name: "KonaClaw", model: "qwen2.5:32b", status: "idle", last_error: null },
  { name: "EmailBot", model: "qwen2.5:14b", status: "thinking", last_error: null },
];

beforeEach(() => {
  postedAgent = null;
  postShouldFail = null;
  patchedAgent = null;
  agentsRows = [
    { name: "KonaClaw", model: "qwen2.5:32b", status: "idle", last_error: null },
    { name: "EmailBot", model: "qwen2.5:14b", status: "thinking", last_error: null },
  ];
});

vi.stubGlobal("fetch", vi.fn(async (url: string, init?: any) => {
  if (url.endsWith("/models")) {
    return { ok: true, json: async () => ({
      models: [
        { name: "gemma3:4b" },
        { name: "qwen2.5:14b" },
        { name: "qwen2.5:32b" },
        { name: "qwen2.5:7b" },
      ],
    }) };
  }
  // PATCH /agents/{name}
  const patchMatch = url.match(/\/agents\/([^/]+)$/);
  if (patchMatch && init?.method === "PATCH") {
    const name = patchMatch[1];
    const body = JSON.parse(init.body);
    patchedAgent = { name, body };
    const row = agentsRows.find(r => r.name === name)!;
    if (body.model) row.model = body.model;
    return { ok: true, json: async () => ({ ...row }) };
  }
  if (url.endsWith("/agents") && init?.method === "POST") {
    if (postShouldFail) {
      const { status, detail } = postShouldFail;
      return { ok: false, status, text: async () => JSON.stringify({ detail }) };
    }
    postedAgent = JSON.parse(init.body);
    return { ok: true, json: async () => ({ name: postedAgent.name, model: postedAgent.model ?? "qwen2.5:7b", status: "idle", last_error: null }) };
  }
  if (url.endsWith("/agents")) return { ok: true, json: async () => ({ agents: agentsRows }) };
  return { ok: true, json: async () => ({}) };
}));

const wrap = (ui: React.ReactNode) =>
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

describe("Agents view", () => {
  it("renders an agent table", async () => {
    render(wrap(<Agents />));
    expect(await screen.findByText(/KonaClaw/)).toBeInTheDocument();
    expect(screen.getByText(/EmailBot/)).toBeInTheDocument();
    expect(screen.getByText(/idle/)).toBeInTheDocument();
    expect(screen.getByText(/thinking/)).toBeInTheDocument();
  });

  it("submits a valid new agent and POSTs to /agents", async () => {
    render(wrap(<Agents />));
    fireEvent.click(await screen.findByText(/\+ New Agent/));

    fireEvent.change(screen.getByPlaceholderText(/my-helper/), { target: { value: "kona" } });
    fireEvent.change(screen.getByPlaceholderText(/You are a helpful/), {
      target: { value: "be helpful" },
    });
    fireEvent.click(screen.getByText(/^Create$/));

    await waitFor(() => expect(postedAgent).not.toBeNull());
    expect(postedAgent.name).toBe("kona");
    expect(postedAgent.system_prompt).toBe("be helpful");
    expect(postedAgent.model).toBeUndefined();
  });

  it("rejects invalid name client-side without POSTing", async () => {
    render(wrap(<Agents />));
    fireEvent.click(await screen.findByText(/\+ New Agent/));
    fireEvent.change(screen.getByPlaceholderText(/my-helper/), { target: { value: "1bad" } });
    fireEvent.change(screen.getByPlaceholderText(/You are a helpful/), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByText(/^Create$/));

    expect(await screen.findByText(/Name must start with a letter/)).toBeInTheDocument();
    expect(postedAgent).toBeNull();
  });

  it("surfaces server error detail (e.g. 409 already exists)", async () => {
    postShouldFail = { status: 409, detail: "agent 'kona' already exists" };
    render(wrap(<Agents />));
    fireEvent.click(await screen.findByText(/\+ New Agent/));
    fireEvent.change(screen.getByPlaceholderText(/my-helper/), { target: { value: "kona" } });
    fireEvent.change(screen.getByPlaceholderText(/You are a helpful/), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByText(/^Create$/));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });

  it("renders a model dropdown for each agent", async () => {
    render(wrap(<Agents />));
    const konaSelect = await screen.findByLabelText(/model for KonaClaw/i) as HTMLSelectElement;
    const emailSelect = await screen.findByLabelText(/model for EmailBot/i) as HTMLSelectElement;
    expect(konaSelect.tagName).toBe("SELECT");
    expect(konaSelect.value).toBe("qwen2.5:32b");
    expect(emailSelect.value).toBe("qwen2.5:14b");
    // dropdown options come from /models
    const optionTexts = Array.from(konaSelect.options).map(o => o.value);
    expect(optionTexts).toContain("gemma3:4b");
    expect(optionTexts).toContain("qwen2.5:7b");
  });

  it("changing the dropdown calls updateAgent and optimistically updates the row", async () => {
    render(wrap(<Agents />));
    const konaSelect = await screen.findByLabelText(/model for KonaClaw/i) as HTMLSelectElement;
    fireEvent.change(konaSelect, { target: { value: "qwen2.5:7b" } });

    await waitFor(() => expect(patchedAgent).not.toBeNull());
    expect(patchedAgent!.name).toBe("KonaClaw");
    expect(patchedAgent!.body).toEqual({ model: "qwen2.5:7b" });

    // Optimistic update: select reflects new value before refetch lands
    await waitFor(() => {
      const refreshed = screen.getByLabelText(/model for KonaClaw/i) as HTMLSelectElement;
      expect(refreshed.value).toBe("qwen2.5:7b");
    });
  });

  it("renders the tool-support footer note", async () => {
    render(wrap(<Agents />));
    expect(await screen.findByText(/Tool support varies by model/)).toBeInTheDocument();
  });
});
