import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SubagentActiveRunsPanel } from "./SubagentActiveRunsPanel";

// Hoist mock refs so we can control them per-test
const mockListActiveSubagents = vi.fn();
const mockStopSubagent = vi.fn();

vi.mock("../api/subagents", () => ({
  listActiveSubagents: (...args: unknown[]) => mockListActiveSubagents(...args),
  stopSubagent: (...args: unknown[]) => mockStopSubagent(...args),
}));

describe("SubagentActiveRunsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows empty state when no subagents are running", async () => {
    mockListActiveSubagents.mockResolvedValue([]);

    render(<SubagentActiveRunsPanel />);

    // Flush the initial fetch
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("No subagents running.")).toBeInTheDocument();
  });

  it("renders active runs with Stop button", async () => {
    mockListActiveSubagents.mockResolvedValue([
      {
        subagent_id: "ep_abc",
        template: "researcher",
        label: "task-1",
        parent_conversation_id: "conv_1",
        tool_calls_used: 5,
      },
    ]);

    render(<SubagentActiveRunsPanel />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("active-run-ep_abc")).toBeInTheDocument();
    expect(screen.getByText("ep_abc")).toBeInTheDocument();
    expect(screen.getByText("researcher")).toBeInTheDocument();
    expect(screen.getByText(/task-1/)).toBeInTheDocument();
    expect(screen.getByText(/5 tools/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("clicking Stop calls stopSubagent and removes row", async () => {
    mockListActiveSubagents.mockResolvedValue([
      {
        subagent_id: "ep_xyz",
        template: "coder",
        label: null,
        parent_conversation_id: "conv_2",
        tool_calls_used: 2,
      },
    ]);
    mockStopSubagent.mockResolvedValue({ stopped: true });

    render(<SubagentActiveRunsPanel />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("active-run-ep_xyz")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop/i }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(mockStopSubagent).toHaveBeenCalledWith("ep_xyz");
    expect(screen.queryByTestId("active-run-ep_xyz")).not.toBeInTheDocument();
  });

  it("shows error message when listActiveSubagents fails", async () => {
    mockListActiveSubagents.mockRejectedValue(new Error("Network error"));

    render(<SubagentActiveRunsPanel />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent("Network error");
  });

  it("polls every 1.5s", async () => {
    mockListActiveSubagents.mockResolvedValue([]);

    render(<SubagentActiveRunsPanel />);

    // Initial call
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockListActiveSubagents).toHaveBeenCalledTimes(1);

    // Advance timer by 1500ms to trigger second poll
    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });
    expect(mockListActiveSubagents).toHaveBeenCalledTimes(2);

    // Advance timer another 1500ms for third poll
    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });
    expect(mockListActiveSubagents).toHaveBeenCalledTimes(3);
  });

  it("shows polling interval in header", () => {
    mockListActiveSubagents.mockResolvedValue([]);

    render(<SubagentActiveRunsPanel />);

    expect(screen.getByText(/polling · 1\.5s/)).toBeInTheDocument();
  });

  it("renders accessible region with correct label", () => {
    mockListActiveSubagents.mockResolvedValue([]);

    render(<SubagentActiveRunsPanel />);

    expect(
      screen.getByRole("region", { name: "Active subagent runs" }),
    ).toBeInTheDocument();
  });
});
