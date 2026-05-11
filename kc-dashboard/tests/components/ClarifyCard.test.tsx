import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { ClarifyCard } from "../../src/components/ClarifyCard";

describe("ClarifyCard", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("renders question and choice buttons", () => {
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Which day?"
                   choices={["Mon", "Tue", "Wed"]} timeout_seconds={300}
                   started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    expect(getByText(/Which day\?/)).toBeTruthy();
    expect(getByText("Mon")).toBeTruthy();
    expect(getByText("Tue")).toBeTruthy();
    expect(getByText("Wed")).toBeTruthy();
    expect(getByText(/Skip/i)).toBeTruthy();
  });

  it("calls onChoose with the picked choice", () => {
    const onChoose = vi.fn();
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={300} started_at={Date.now() / 1000}
                   onChoose={onChoose} onSkip={vi.fn()} />
    );
    fireEvent.click(getByText("B"));
    expect(onChoose).toHaveBeenCalledWith("r1", "B");
  });

  it("calls onSkip on the Skip button", () => {
    const onSkip = vi.fn();
    const { getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={300} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={onSkip} />
    );
    fireEvent.click(getByText(/Skip/i));
    expect(onSkip).toHaveBeenCalledWith("r1");
  });

  it("countdown decrements once per second", () => {
    const { container } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={10} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    expect(container.textContent).toContain("0:10");
    act(() => { vi.advanceTimersByTime(1000); });
    expect(container.textContent).toContain("0:09");
  });

  it("disables buttons after timeout reaches 0 and shows 'Timed out'", () => {
    const { container, getByText } = render(
      <ClarifyCard request_id="r1" question="Q?" choices={["A", "B"]}
                   timeout_seconds={2} started_at={Date.now() / 1000}
                   onChoose={vi.fn()} onSkip={vi.fn()} />
    );
    act(() => { vi.advanceTimersByTime(2500); });
    expect(container.textContent).toMatch(/Timed out/i);
    const btnA = getByText("A").closest("button");
    expect(btnA?.disabled).toBe(true);
  });
});
