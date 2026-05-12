import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SubagentTraceBlock } from "./SubagentTraceBlock";

const STARTED = {
  subagent_id: "ep_abc123",
  template: "researcher",
  label: null,
  task_preview: "Find info about React hooks",
};

const TOOLS = [
  { tool: "web_search", args_preview: '{"query":"React hooks"}', result_preview: "Found 10 results" },
  { tool: "read_file", tier: "t1" },
];

const FINISHED_OK = {
  status: "ok" as const,
  reply_preview: "Here is what I found about React hooks.",
  duration_ms: 3200,
  tool_calls_used: 2,
  error_message: null,
};

const FINISHED_ERROR = {
  status: "error" as const,
  reply_preview: "",
  duration_ms: 1500,
  tool_calls_used: 1,
  error_message: "Rate limit exceeded",
};

describe("SubagentTraceBlock", () => {
  it("shows 'running…' and Stop button in running state", () => {
    const onStop = vi.fn();
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={null}
        onStop={onStop}
      />,
    );
    expect(screen.getByText("running…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("clicking Stop fires onStop callback", () => {
    const onStop = vi.fn();
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={null}
        onStop={onStop}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("does not propagate Stop click to header (no collapse)", () => {
    const onStop = vi.fn();
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={null}
        onStop={onStop}
      />,
    );
    // Body is expanded initially
    expect(screen.getByText(/Find info about React hooks/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    // Body should still be visible after clicking Stop (no collapse)
    expect(screen.getByText(/Find info about React hooks/)).toBeInTheDocument();
  });

  it("finished ok state shows ✓ icon and reply_preview body", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={FINISHED_OK}
        onStop={undefined}
      />,
    );
    // Status text in header span contains "✓ ok · N tools · Xs"
    const headerStatus = screen.getByText((content, el) =>
      el?.tagName === "SPAN" && /✓/.test(content) && /ok/.test(content),
    );
    expect(headerStatus).toBeInTheDocument();
    // Reply preview in body
    expect(screen.getByText("Here is what I found about React hooks.")).toBeInTheDocument();
    // No Stop button when finished
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  it("finished error state shows error_message in body", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={FINISHED_ERROR}
        onStop={undefined}
      />,
    );
    expect(screen.getByText("Rate limit exceeded")).toBeInTheDocument();
  });

  it("header toggle collapses body on click and expands on second click", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={[]}
        finished={FINISHED_OK}
        onStop={undefined}
      />,
    );
    // Body visible initially
    expect(screen.getByText("Here is what I found about React hooks.")).toBeInTheDocument();

    // Click header to collapse
    fireEvent.click(screen.getByRole("banner").querySelector("header") ?? screen.getByText(/subagent: researcher/));
    expect(screen.queryByText("Here is what I found about React hooks.")).not.toBeInTheDocument();

    // Click header again to expand
    fireEvent.click(screen.getByText(/subagent: researcher/));
    expect(screen.getByText("Here is what I found about React hooks.")).toBeInTheDocument();
  });

  it("shows template label in header when provided", () => {
    render(
      <SubagentTraceBlock
        started={{ ...STARTED, label: "phase-1" }}
        tools={[]}
        finished={null}
        onStop={undefined}
      />,
    );
    expect(screen.getByText(/researcher · phase-1/)).toBeInTheDocument();
  });

  it("shows duration and tool count in finished header", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={FINISHED_OK}
        onStop={undefined}
      />,
    );
    // "2 tools" and "3.2s"
    expect(screen.getByText(/2 tools/)).toBeInTheDocument();
    expect(screen.getByText(/3\.2s/)).toBeInTheDocument();
  });

  it("shows task_preview in body", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={[]}
        finished={null}
        onStop={undefined}
      />,
    );
    expect(screen.getByText(/Find info about React hooks/)).toBeInTheDocument();
  });

  it("renders tool rows with args and result previews", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={TOOLS}
        finished={null}
        onStop={undefined}
      />,
    );
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText(/args:.*React hooks/)).toBeInTheDocument();
    expect(screen.getByText(/Found 10 results/)).toBeInTheDocument();
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText(/\[t1\]/)).toBeInTheDocument();
  });

  it("has accessible aria-label with subagent_id", () => {
    render(
      <SubagentTraceBlock
        started={STARTED}
        tools={[]}
        finished={null}
        onStop={undefined}
      />,
    );
    expect(screen.getByRole("region", { name: /Subagent trace ep_abc123/ })).toBeInTheDocument();
  });
});
