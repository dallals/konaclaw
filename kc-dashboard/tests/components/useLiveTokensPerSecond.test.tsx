import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLiveTokensPerSecond } from "../../src/ws/useLiveTokensPerSecond";

describe("useLiveTokensPerSecond", () => {
  it("returns null when streaming buffer is empty", () => {
    const { result } = renderHook(() => useLiveTokensPerSecond("", null));
    expect(result.current).toBeNull();
  });

  it("computes chars/4 / elapsed seconds", () => {
    vi.useFakeTimers();
    const t0 = Date.now();
    const { result, rerender } = renderHook(
      ({ buf, start }: { buf: string; start: number | null }) =>
        useLiveTokensPerSecond(buf, start),
      { initialProps: { buf: "", start: null } },
    );
    rerender({ buf: "0123456789".repeat(10), start: t0 }); // 100 chars
    act(() => { vi.setSystemTime(t0 + 1000); vi.advanceTimersByTime(250); });
    // 100/4 = 25 estimated tokens; tick advances clock to t0+1250 → 25/1.25 = 20 t/s
    expect(result.current).toBeCloseTo(20, 0);
    vi.useRealTimers();
  });
});
