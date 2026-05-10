import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Reminders from "./Reminders";

vi.mock("../api/reminders", () => ({
  listReminders: vi.fn().mockResolvedValue({
    reminders: [
      { id: 1, kind: "reminder", payload: "stretch", channel: "telegram",
        status: "pending", agent: "kona", conversation_id: 1, chat_id: "c",
        when_utc: Date.now()/1000 + 600, cron_spec: null, attempts: 0,
        last_fired_at: null, created_at: Date.now()/1000, mode: "literal",
        next_fire_at: Date.now()/1000 + 600 },
      { id: 2, kind: "cron", payload: "standup", channel: "dashboard",
        status: "pending", agent: "kona", conversation_id: 1, chat_id: "c",
        when_utc: null, cron_spec: "0 9 * * *", attempts: 0,
        last_fired_at: null, created_at: Date.now()/1000, mode: "literal",
        next_fire_at: Date.now()/1000 + 9000 },
    ],
  }),
  cancelReminder: vi.fn(),
  snoozeReminder: vi.fn(),
}));
vi.mock("../ws/useReminderEvents", () => ({ useReminderEvents: () => {} }));

function renderView(initial = "/reminders") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Reminders />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Reminders view", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders rows from the API", async () => {
    renderView();
    await waitFor(() => expect(screen.getByText("stretch")).toBeInTheDocument());
    expect(screen.getByText("standup")).toBeInTheDocument();
  });

  it("filters to one-shots when the One-shot tab is clicked", async () => {
    renderView();
    await waitFor(() => screen.getByText("stretch"));
    fireEvent.click(screen.getByRole("tab", { name: /one-shot/i }));
    const { listReminders } = await import("../api/reminders");
    await waitFor(() => {
      const lastCall = (listReminders as any).mock.calls.at(-1)[0];
      expect(lastCall).toEqual(expect.objectContaining({ kinds: ["reminder"] }));
    });
  });

  it("clicking a status chip toggles it in the URL params", async () => {
    renderView();
    await waitFor(() => screen.getByText("stretch"));
    fireEvent.click(screen.getByRole("button", { name: /^pending$/i }));
    const { listReminders } = await import("../api/reminders");
    await waitFor(() => {
      const lastCall = (listReminders as any).mock.calls.at(-1)[0];
      expect(lastCall.statuses).toEqual(["pending"]);
    });
  });

  it("clicking a row toggles the audit panel", async () => {
    renderView();
    await waitFor(() => screen.getByText("stretch"));
    expect(screen.queryByText(/Created at/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("stretch"));
    expect(screen.getByText(/Created at/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("stretch"));
    expect(screen.queryByText(/Created at/)).not.toBeInTheDocument();
  });
});
