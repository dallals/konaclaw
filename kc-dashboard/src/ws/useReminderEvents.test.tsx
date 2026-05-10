import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useReminderEvents } from "./useReminderEvents";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  close() { this.onclose?.(); }
  send() {}
}

describe("useReminderEvents", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket as any;
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it("invalidates reminders queries on a reminder.created event", async () => {
    const qc = new QueryClient();
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    renderHook(() => useReminderEvents(), { wrapper });
    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.onmessage?.(new MessageEvent("message", {
        data: JSON.stringify({ type: "reminder.created", reminder: { id: 1 }, ts: 1 }),
      }));
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["reminders"] });
  });
});
