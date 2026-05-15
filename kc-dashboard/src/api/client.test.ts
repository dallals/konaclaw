import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, apiPatch, apiDelete, getBaseUrl } from "./client";

describe("client network-error translation", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("apiGet translates fetch TypeError into a supervisor-unreachable message", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("Load failed"));
    await expect(apiGet("/models")).rejects.toThrow(
      new RegExp(`Cannot reach KonaClaw supervisor at ${getBaseUrl()}`),
    );
  });

  it("apiPost translates fetch TypeError into a supervisor-unreachable message", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("Load failed"));
    await expect(apiPost("/agents", {})).rejects.toThrow(
      /Cannot reach KonaClaw supervisor at .* — is it running\?/,
    );
  });

  it("apiPatch translates fetch TypeError into a supervisor-unreachable message", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(apiPatch("/agents/x", {})).rejects.toThrow(
      /Cannot reach KonaClaw supervisor/,
    );
  });

  it("apiDelete translates fetch TypeError into a supervisor-unreachable message", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("NetworkError"));
    await expect(apiDelete("/agents/x")).rejects.toThrow(
      /Cannot reach KonaClaw supervisor/,
    );
  });

  it("apiPost still surfaces HTTP error bodies with the original → NNN: format", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("boom", { status: 500 }) as Response,
    );
    await expect(apiPost("/agents", {})).rejects.toThrow(/→ 500: boom/);
  });
});
