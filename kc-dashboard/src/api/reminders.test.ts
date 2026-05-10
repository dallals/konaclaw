import { describe, it, expect, vi, beforeEach } from "vitest";
import { listReminders, cancelReminder, snoozeReminder } from "./reminders";

describe("reminders api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("listReminders builds query params", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ reminders: [] }), { status: 200 }) as Response,
    );
    await listReminders({ statuses: ["pending"], kinds: ["reminder"], channels: ["dashboard"] });
    const url = (fetchMock.mock.calls[0][0] as string);
    expect(url).toContain("/reminders?");
    expect(url).toContain("status=pending");
    expect(url).toContain("kind=reminder");
    expect(url).toContain("channel=dashboard");
  });

  it("cancelReminder sends DELETE", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response(null, { status: 204 }) as Response);
    await cancelReminder(42);
    expect(fetchMock.mock.calls[0][0]).toContain("/reminders/42");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });

  it("snoozeReminder sends PATCH with when_utc", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: 42, when_utc: 1234 }), { status: 200 }) as Response,
    );
    await snoozeReminder(42, 1234);
    expect(fetchMock.mock.calls[0][0]).toContain("/reminders/42");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("PATCH");
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(JSON.stringify({ when_utc: 1234 }));
  });
});
