import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MessageBubble } from "./MessageBubble";

describe("MessageBubble reasoning", () => {
  it("renders no reasoning block when reasoning prop is absent", () => {
    render(<MessageBubble role="assistant" content="Hello" />);
    expect(screen.queryByText(/thinking|reasoning/i)).not.toBeInTheDocument();
  });

  it("renders no reasoning block when reasoning is empty/whitespace", () => {
    render(<MessageBubble role="assistant" content="Hello" reasoning="   " />);
    expect(screen.queryByText(/thinking|reasoning/i)).not.toBeInTheDocument();
  });

  it("renders an expanded reasoning details block while still streaming reasoning", () => {
    render(
      <MessageBubble
        role="assistant"
        content=""
        reasoning="Step 1: think about the problem."
      />,
    );
    const details = screen.getByText(/step 1: think about the problem/i)
      .closest("details");
    expect(details).not.toBeNull();
    expect(details!.hasAttribute("open")).toBe(true);
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });

  it("renders a collapsed reasoning details block once content has started", () => {
    render(
      <MessageBubble
        role="assistant"
        content="Here is the answer."
        reasoning="Step 1: think about the problem."
      />,
    );
    const details = screen.getByText(/step 1: think about the problem/i)
      .closest("details");
    expect(details).not.toBeNull();
    expect(details!.hasAttribute("open")).toBe(false);
    expect(screen.getByText(/reasoning/i)).toBeInTheDocument();
    // Final answer still renders alongside the collapsed reasoning.
    expect(screen.getByText(/here is the answer/i)).toBeInTheDocument();
  });

  it("renders attachment chips for past user message with [attached:] prefix", () => {
    const { container } = render(
      <MessageBubble
        role="user"
        content={"[attached: report.pdf, 12 pages, 245 KB, id=att_abc]\nHi there"}
      />,
    );
    expect(screen.getByText(/report\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/Hi there/)).toBeInTheDocument();
    // The raw bracket form must NOT appear in the rendered output — only the chip.
    expect(container.textContent ?? "").not.toMatch(/\[attached:/);
  });

  it("renders multiple chips when multiple attachments present", () => {
    const { container } = render(
      <MessageBubble
        role="user"
        content={"[attached: a.txt, 1 KB, id=att_a]\n[attached: b.png, 2 KB, id=att_b]\nLook at these"}
      />,
    );
    expect(screen.getByText(/a\.txt/)).toBeInTheDocument();
    expect(screen.getByText(/b\.png/)).toBeInTheDocument();
    expect(screen.getByText(/Look at these/)).toBeInTheDocument();
    expect(container.textContent ?? "").not.toMatch(/\[attached:/);
  });

  it("renders content normally when no [attached:] prefix", () => {
    render(<MessageBubble role="user" content="just a regular message" />);
    expect(screen.getByText(/just a regular message/)).toBeInTheDocument();
  });
});
