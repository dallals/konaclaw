import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { ChatProgressIndicator, type ToolCallState } from "./ChatProgressIndicator";


function call(overrides: Partial<ToolCallState> = {}): ToolCallState {
  return {
    id: overrides.id ?? "call_1",
    name: overrides.name ?? "read_attachment",
    args: overrides.args ?? {},
    status: overrides.status ?? "running",
  };
}


describe("ChatProgressIndicator", () => {
  it("renders Thinking when no tool calls", () => {
    render(<ChatProgressIndicator toolCalls={[]} attachmentFilenames={{}} />);
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });

  it("renders read_attachment label with resolved filename", () => {
    const calls = [call({ args: { attachment_id: "att_abc" } })];
    render(
      <ChatProgressIndicator
        toolCalls={calls}
        attachmentFilenames={{ att_abc: "will.pdf" }}
      />,
    );
    expect(screen.getByText(/reading will\.pdf/i)).toBeInTheDocument();
  });

  it("renders read_attachment label without filename (unresolved)", () => {
    const calls = [call({ args: { attachment_id: "att_xyz" } })];
    render(
      <ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />,
    );
    expect(screen.getByText(/reading attachment/i)).toBeInTheDocument();
  });

  it("renders web_search label", () => {
    const calls = [call({ name: "web_search", args: { query: "weather" } })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/searching the web/i)).toBeInTheDocument();
  });

  it("renders web_fetch label with truncated host", () => {
    const calls = [call({ name: "web_fetch", args: { url: "https://en.wikipedia.org/wiki/Claude_Shannon" } })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/fetching en\.wikipedia\.org/i)).toBeInTheDocument();
  });

  it("falls back to Running {name} for unknown tools", () => {
    const calls = [call({ name: "weird_tool" })];
    render(<ChatProgressIndicator toolCalls={calls} attachmentFilenames={{}} />);
    expect(screen.getByText(/running weird_tool/i)).toBeInTheDocument();
  });

  it("uses most-recent running call for top label when multiple in flight", () => {
    const calls = [
      call({ id: "call_1", name: "web_search", args: { query: "x" }, status: "done" }),
      call({ id: "call_2", name: "read_attachment", args: { attachment_id: "att_abc" } }),
    ];
    render(
      <ChatProgressIndicator
        toolCalls={calls}
        attachmentFilenames={{ att_abc: "will.pdf" }}
      />,
    );
    expect(screen.getByText(/reading will\.pdf/i)).toBeInTheDocument();
  });
});
