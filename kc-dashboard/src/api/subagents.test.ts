import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  listSubagentTemplates,
  getSubagentTemplate,
  createSubagentTemplate,
  updateSubagentTemplate,
  deleteSubagentTemplate,
  listActiveSubagents,
  stopSubagent,
} from "./subagents";

describe("subagents api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("listSubagentTemplates calls GET /subagent-templates", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }) as Response,
    );
    const result = await listSubagentTemplates();
    expect(fetchMock.mock.calls[0][0]).toContain("/subagent-templates");
    expect(result).toEqual([]);
  });

  it("getSubagentTemplate calls GET /subagent-templates/{name}", async () => {
    const detail = { name: "my-agent", yaml: "name: my-agent\n" };
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(detail), { status: 200 }) as Response,
    );
    const result = await getSubagentTemplate("my-agent");
    expect(fetchMock.mock.calls[0][0]).toContain("/subagent-templates/my-agent");
    expect(result).toEqual(detail);
  });

  it("getSubagentTemplate url-encodes the name", async () => {
    const detail = { name: "my agent", yaml: "name: my agent\n" };
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(detail), { status: 200 }) as Response,
    );
    await getSubagentTemplate("my agent");
    expect(fetchMock.mock.calls[0][0]).toContain("/subagent-templates/my%20agent");
  });

  it("createSubagentTemplate sends POST with yaml body", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "new-agent" }), { status: 201 }) as Response,
    );
    const result = await createSubagentTemplate("name: new-agent\n");
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toContain("/subagent-templates");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect((call[1] as RequestInit).body).toBe(JSON.stringify({ yaml: "name: new-agent\n" }));
    expect(result).toEqual({ name: "new-agent" });
  });

  it("updateSubagentTemplate sends PATCH with yaml body", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ name: "my-agent" }), { status: 200 }) as Response,
    );
    const result = await updateSubagentTemplate("my-agent", "name: my-agent\nversion: 2\n");
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toContain("/subagent-templates/my-agent");
    expect((call[1] as RequestInit).method).toBe("PATCH");
    expect((call[1] as RequestInit).body).toBe(JSON.stringify({ yaml: "name: my-agent\nversion: 2\n" }));
    expect(result).toEqual({ name: "my-agent" });
  });

  it("deleteSubagentTemplate sends DELETE", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }) as Response,
    );
    await deleteSubagentTemplate("my-agent");
    expect(fetchMock.mock.calls[0][0]).toContain("/subagent-templates/my-agent");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });

  it("listActiveSubagents calls GET /subagents/active", async () => {
    const active = [
      {
        subagent_id: "abc-123",
        template: "my-agent",
        label: "research task",
        parent_conversation_id: "conv-456",
        tool_calls_used: 3,
      },
    ];
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(active), { status: 200 }) as Response,
    );
    const result = await listActiveSubagents();
    expect(fetchMock.mock.calls[0][0]).toContain("/subagents/active");
    expect(result).toEqual(active);
  });

  it("stopSubagent sends POST to /subagents/{id}/stop", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ stopped: true }), { status: 200 }) as Response,
    );
    const result = await stopSubagent("abc-123");
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toContain("/subagents/abc-123/stop");
    expect((call[1] as RequestInit).method).toBe("POST");
    expect(result).toEqual({ stopped: true });
  });
});
