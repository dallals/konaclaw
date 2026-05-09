import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "../../src/components/MessageBubble";

describe("MessageBubble usage badge", () => {
  it("renders badge with tok/s, count, calls suffix, ttfb", () => {
    render(
      <MessageBubble
        role="assistant"
        content="hi"
        usage={{
          input_tokens: 100,
          output_tokens: 412,
          ttfb_ms: 1042,
          generation_ms: 3240,
          calls: 2,
          usage_reported: true,
        }}
      />,
    );
    // 412 tokens / 3.24 s ≈ 127 t/s
    expect(screen.getByText(/127 t\/s/)).toBeInTheDocument();
    expect(screen.getByText(/412 tok/)).toBeInTheDocument();
    expect(screen.getByText(/2 calls/)).toBeInTheDocument();
    expect(screen.getByText(/ttfb 1\.04 s/)).toBeInTheDocument();
  });

  it("omits 'calls' suffix when calls === 1", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: 100, output_tokens: 50, ttfb_ms: 100, generation_ms: 1000,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.queryByText(/1 calls/)).not.toBeInTheDocument();
  });

  it("renders '— · ttfb …' for tool-only turn (output_tokens=0)", () => {
    render(
      <MessageBubble role="assistant" content="" usage={{
        input_tokens: 100, output_tokens: 0, ttfb_ms: 200, generation_ms: 0,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.getByText(/—/)).toBeInTheDocument();
    expect(screen.getByText(/ttfb 0\.20 s/)).toBeInTheDocument();
  });

  it("renders '— ttfb only' when usage_reported is false", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: null, output_tokens: null, ttfb_ms: 1000, generation_ms: 500,
        calls: 1, usage_reported: false,
      }} />,
    );
    expect(screen.getByText(/ttfb only/)).toBeInTheDocument();
  });

  it("renders 'instant' when generation_ms < 50", () => {
    render(
      <MessageBubble role="assistant" content="hi" usage={{
        input_tokens: 100, output_tokens: 4, ttfb_ms: 50, generation_ms: 10,
        calls: 1, usage_reported: true,
      }} />,
    );
    expect(screen.getByText(/instant/)).toBeInTheDocument();
  });

  it("renders no badge when usage prop is omitted", () => {
    render(<MessageBubble role="assistant" content="hi" />);
    expect(screen.queryByText(/t\/s/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ttfb/)).not.toBeInTheDocument();
  });
});
