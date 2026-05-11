import { describe, it, expect, vi, beforeEach } from "vitest";
import { listTodos, createTodo, patchTodo, deleteTodo, bulkDeleteTodos } from "../../src/api/todos";

describe("todos api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listTodos sends correct query and parses response", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ id: 1, title: "A" }], count: 1 }),
    } as any);
    const res = await listTodos({ conversationId: 40, agent: "Kona-AI" });
    expect(res.count).toBe(1);
    expect(res.items[0].title).toBe("A");
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos?");
    expect(fetchSpy.mock.calls[0][0]).toContain("conversation_id=40");
    // agent must NOT be sent — supervisor resolves it from the conversation row.
    expect(fetchSpy.mock.calls[0][0]).not.toContain("agent=");
  });

  it("createTodo POSTs JSON body without agent", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1, title: "A", scope: "conversation" }),
    } as any);
    await createTodo({ conversationId: 40, agent: "Kona-AI", title: "A" });
    const args = fetchSpy.mock.calls[0];
    expect(args[1].method).toBe("POST");
    const body = JSON.parse(args[1].body);
    // agent must NOT be in the POST body.
    expect(body).not.toHaveProperty("agent");
    expect(body).toEqual({
      conversation_id: 40, title: "A", notes: "", persist: false,
    });
  });

  it("patchTodo PATCHes /todos/:id without agent in body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ id: 1, status: "done" }),
    } as any);
    await patchTodo({ id: 1, conversationId: 40, agent: "Kona-AI", status: "done" });
    const args = fetchSpy.mock.calls[0];
    expect(args[0]).toContain("/todos/1");
    expect(args[1].method).toBe("PATCH");
    // agent must NOT appear in the PATCH body.
    expect(JSON.parse(args[1].body)).not.toHaveProperty("agent");
  });

  it("deleteTodo sends DELETE without agent param", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, status: 204, text: async () => "",
    } as any);
    await deleteTodo({ id: 5, conversationId: 40, agent: "Kona-AI" });
    expect(fetchSpy.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos/5");
    // agent must NOT be in the query string.
    expect(fetchSpy.mock.calls[0][0]).not.toContain("agent=");
  });

  it("bulkDeleteTodos sends DELETE with query params without agent", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ deleted_count: 3 }),
    } as any);
    const res = await bulkDeleteTodos({
      conversationId: 40, agent: "Kona-AI", scope: "all", status: "done",
    });
    expect(res.deleted_count).toBe(3);
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos?");
    expect(fetchSpy.mock.calls[0][0]).toContain("status=done");
    // agent must NOT be in the query string.
    expect(fetchSpy.mock.calls[0][0]).not.toContain("agent=");
  });
});
