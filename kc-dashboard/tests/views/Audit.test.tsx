import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Audit from "../../src/views/Audit";

vi.stubGlobal("fetch", vi.fn(async () => ({
  ok: true, json: async () => ({ entries: [
    { id: 1, ts: 1000, agent: "kc", tool: "file.read", args_json: "{}", decision: "safe·auto", result: "ok", undoable: 0, undone: 0 },
    { id: 2, ts: 1001, agent: "kc", tool: "file.delete", args_json: "{}", decision: "destructive·user-approved", result: "ok", undoable: 1, undone: 0 },
    { id: 3, ts: 1002, agent: "kc", tool: "memory.append", args_json: "{}", decision: "tier", result: "ok", undoable: 1, undone: 1 },
  ] })
})));

const wrap = (ui: React.ReactNode) =>
  <MemoryRouter><QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider></MemoryRouter>;

describe("Audit view", () => {
  it("renders rows with Undo only on undoable + not-yet-undone", async () => {
    render(wrap(<Audit />));
    expect(await screen.findByText(/file.read/)).toBeInTheDocument();
    expect(screen.getByText(/file.delete/)).toBeInTheDocument();
    expect(screen.getByText(/memory.append/)).toBeInTheDocument();
    // file.read isn't undoable → "—"
    // file.delete is undoable + not undone → "↩ Undo"
    // memory.append is undoable + already undone → "✓ undone"
    expect(screen.getAllByText(/↩ Undo/i)).toHaveLength(1);
    expect(screen.getByText(/✓ undone/i)).toBeInTheDocument();
  });
});
