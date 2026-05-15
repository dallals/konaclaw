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
});
