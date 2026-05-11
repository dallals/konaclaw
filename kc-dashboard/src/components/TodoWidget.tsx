import { useEffect, useState } from "react";
import { listTodos, patchTodo, deleteTodo, type Todo } from "../api/todos";
import { TodoItem } from "./TodoItem";

export type TodoWidgetProps = {
  conversationId: number;
  agent: string;
  refreshKey?: number;
};

export function TodoWidget({ conversationId, agent, refreshKey }: TodoWidgetProps) {
  const [items, setItems] = useState<Todo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refetch = async () => {
    try {
      const res = await listTodos({ conversationId, agent });
      setItems(res.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => { void refetch(); }, [conversationId, agent, refreshKey]);

  if (error) return <div style={{ padding: 8, color: "#e57373" }}>todos: {error}</div>;
  if (items == null) return <div style={{ padding: 8, color: "#888" }}>Loading…</div>;
  if (items.length === 0) {
    return (
      <div style={{ padding: 8, color: "#888", fontStyle: "italic", fontSize: 11 }}>
        No todos yet — ask Kona to start a list.
      </div>
    );
  }

  const convItems  = items.filter((t) => t.scope === "conversation");
  const agentItems = items.filter((t) => t.scope === "agent");

  const onToggle = async (id: number, status: "open" | "done") => {
    setItems((prev) => prev?.map((t) => t.id === id ? { ...t, status } : t) ?? null);
    try {
      await patchTodo({ id, conversationId, agent, status });
    } catch {
      void refetch();
    }
  };

  const onDelete = async (id: number) => {
    setItems((prev) => prev?.filter((t) => t.id !== id) ?? null);
    try {
      await deleteTodo({ id, conversationId, agent });
    } catch {
      void refetch();
    }
  };

  const onEdit = (_id: number) => {
    // Inline-edit popover is a v2 polish. For now, the dashboard offers
    // status-toggle and delete; edits flow through Kona via chat.
  };

  return (
    <div style={{ padding: 6 }}>
      <div style={{
        fontSize: 11, color: "#aaa", marginBottom: 6,
        textTransform: "uppercase", letterSpacing: 1,
      }}>Todo</div>
      {convItems.map((t) => (
        <TodoItem key={t.id} todo={t}
                  onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
      ))}
      {agentItems.length > 0 && (
        <>
          <div style={{
            fontSize: 10, color: "#4a90e2", marginTop: 8, marginBottom: 4,
            fontWeight: 600,
          }}>📌 Persistent</div>
          {agentItems.map((t) => (
            <TodoItem key={t.id} todo={t}
                      onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
          ))}
        </>
      )}
    </div>
  );
}
