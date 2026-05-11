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
    expect(fetchSpy.mock.calls[0][0]).toContain("agent=Kona-AI");
  });

  it("createTodo POSTs JSON body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1, title: "A", scope: "conversation" }),
    } as any);
    await createTodo({ conversationId: 40, agent: "Kona-AI", title: "A" });
    const args = fetchSpy.mock.calls[0];
    expect(args[1].method).toBe("POST");
    expect(JSON.parse(args[1].body)).toEqual({
      conversation_id: 40, agent: "Kona-AI", title: "A", notes: "", persist: false,
    });
  });

  it("patchTodo PATCHes /todos/:id", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ id: 1, status: "done" }),
    } as any);
    await patchTodo({ id: 1, conversationId: 40, agent: "Kona-AI", status: "done" });
    const args = fetchSpy.mock.calls[0];
    expect(args[0]).toContain("/todos/1");
    expect(args[1].method).toBe("PATCH");
  });

  it("deleteTodo sends DELETE", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, status: 204, text: async () => "",
    } as any);
    await deleteTodo({ id: 5, conversationId: 40, agent: "Kona-AI" });
    expect(fetchSpy.mock.calls[0][1].method).toBe("DELETE");
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos/5");
  });

  it("bulkDeleteTodos sends DELETE with query params", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true, json: async () => ({ deleted_count: 3 }),
    } as any);
    const res = await bulkDeleteTodos({
      conversationId: 40, agent: "Kona-AI", scope: "all", status: "done",
    });
    expect(res.deleted_count).toBe(3);
    expect(fetchSpy.mock.calls[0][0]).toContain("/todos?");
    expect(fetchSpy.mock.calls[0][0]).toContain("status=done");
  });
});
