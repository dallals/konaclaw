const BASE = "http://127.0.0.1:8765";

export type Todo = {
  id: number;
  agent: string;
  conversation_id: number | null;
  title: string;
  notes: string;
  status: "open" | "done";
  scope: "conversation" | "agent";
  created_at: number;
  updated_at: number;
};

export async function listTodos(args: {
  conversationId: number;
  agent: string;
  status?: "open" | "done" | "all";
  scope?: "all" | "conversation" | "agent";
}): Promise<{ items: Todo[]; count: number }> {
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
    // agent is intentionally omitted — the supervisor resolves it from
    // the conversation row so callers can't spoof it.
    status: args.status ?? "open",
    scope: args.scope ?? "all",
  });
  const r = await fetch(`${BASE}/todos?${params.toString()}`);
  if (!r.ok) throw new Error(`listTodos failed: ${r.status}`);
  return r.json();
}

export async function createTodo(args: {
  conversationId: number;
  agent: string;
  title: string;
  notes?: string;
  persist?: boolean;
}): Promise<Todo> {
  const r = await fetch(`${BASE}/todos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: args.conversationId,
      // agent omitted — supervisor injects it from the conversation row.
      title: args.title,
      notes: args.notes ?? "",
      persist: args.persist ?? false,
    }),
  });
  if (!r.ok) throw new Error(`createTodo failed: ${r.status}`);
  return r.json();
}

export async function patchTodo(args: {
  id: number;
  conversationId: number;
  agent: string;
  title?: string;
  notes?: string;
  status?: "open" | "done";
}): Promise<Todo> {
  // agent omitted — supervisor injects it from the conversation row.
  const body: Record<string, any> = {
    conversation_id: args.conversationId,
  };
  if (args.title !== undefined) body.title = args.title;
  if (args.notes !== undefined) body.notes = args.notes;
  if (args.status !== undefined) body.status = args.status;
  const r = await fetch(`${BASE}/todos/${args.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patchTodo failed: ${r.status}`);
  return r.json();
}

export async function deleteTodo(args: {
  id: number;
  conversationId: number;
  agent: string;
}): Promise<void> {
  // agent omitted — supervisor injects it from the conversation row.
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
  });
  const r = await fetch(`${BASE}/todos/${args.id}?${params.toString()}`, {
    method: "DELETE",
  });
  if (!r.ok && r.status !== 204) throw new Error(`deleteTodo failed: ${r.status}`);
}

export async function bulkDeleteTodos(args: {
  conversationId: number;
  agent: string;
  scope: "all" | "conversation" | "agent";
  status: "done";
}): Promise<{ deleted_count: number }> {
  // agent omitted — supervisor injects it from the conversation row.
  const params = new URLSearchParams({
    conversation_id: String(args.conversationId),
    scope: args.scope,
    status: args.status,
  });
  const r = await fetch(`${BASE}/todos?${params.toString()}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`bulkDeleteTodos failed: ${r.status}`);
  return r.json();
}
