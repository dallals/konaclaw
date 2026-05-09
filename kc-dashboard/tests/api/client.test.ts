import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, setBaseUrl } from "../../src/api/client";

beforeEach(() => {
  setBaseUrl("http://supervisor.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: any) => ({
    ok: true,
    json: async () => ({ url, init }),
  })));
});

describe("api client", () => {
  it("apiGet calls fetch with the configured base URL", async () => {
    const data = await apiGet<any>("/agents");
    expect(data.url).toBe("http://supervisor.test/agents");
  });

  it("apiPost sends JSON body", async () => {
    const data = await apiPost<any>("/agents/x/conversations", { channel: "dashboard" });
    expect(JSON.parse(data.init.body)).toEqual({ channel: "dashboard" });
    expect(data.init.headers["content-type"]).toBe("application/json");
  });

  it("throws on non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 500, text: async () => "boom" })));
    await expect(apiGet("/x")).rejects.toThrow(/500/);
  });
});
