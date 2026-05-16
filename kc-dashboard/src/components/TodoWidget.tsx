import { useEffect, useState } from "react";
import { listTodos, patchTodo, deleteTodo, type Todo } from "../api/todos";
import { TodoItem } from "./TodoItem";

const LS_COLLAPSED = "kc.todos.collapsed";

export type TodoWidgetProps = {
  conversationId: number;
  agent: string;
  refreshKey?: number;
};

export function TodoWidget({ conversationId, agent, refreshKey }: TodoWidgetProps) {
  const [items, setItems] = useState<Todo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Manual collapse persists across reloads. Empty state auto-collapses unless
  // the user has explicitly expanded it. localStorage holds "1"/"0"/null.
  const [manualCollapsed, setManualCollapsed] = useState<boolean | null>(() => {
    const v = localStorage.getItem(LS_COLLAPSED);
    return v === "1" ? true : v === "0" ? false : null;
  });

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

  const setCollapsed = (v: boolean) => {
    setManualCollapsed(v);
    localStorage.setItem(LS_COLLAPSED, v ? "1" : "0");
  };

  const count = items?.length ?? 0;
  // Auto-collapse when there are no items, unless the user has explicitly
  // expanded. When items exist, default to expanded unless explicitly collapsed.
  const collapsed = manualCollapsed ?? (count === 0);

  if (collapsed) {
    return (
      <aside className="w-10 border-l border-line bg-panel flex flex-col items-center pt-3 gap-2">
        <button
          aria-label="Expand Todos"
          onClick={() => setCollapsed(false)}
          className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted2 hover:text-textStrong"
        >
          ⌃
        </button>
        <div
          className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted2"
          style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
        >
          Todos {count > 0 ? `· ${count}` : ""}
        </div>
      </aside>
    );
  }

  const convItems  = (items ?? []).filter((t) => t.scope === "conversation");
  const agentItems = (items ?? []).filter((t) => t.scope === "agent");

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
    <aside className="w-[280px] shrink-0 border-l border-line bg-panel overflow-y-auto">
      <div className="px-4 pt-3 pb-2 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted2 font-medium">
          Todos {count > 0 ? `· ${count}` : ""}
        </span>
        <button
          aria-label="Collapse Todos"
          onClick={() => setCollapsed(true)}
          className="font-mono text-[10px] text-muted2 hover:text-textStrong"
        >
          ⌃
        </button>
      </div>

      {error && (
        <div className="mx-4 mb-3 px-2 py-1 border border-line text-[12px] text-muted">
          todos: {error}
        </div>
      )}

      {!error && items == null && (
        <div className="mx-4 mb-3 text-[12px] text-muted italic">Loading…</div>
      )}

      {!error && items != null && count === 0 && (
        <div className="mx-4 mb-3 text-[12px] text-muted italic">
          No todos yet — ask Kona to start a list.
        </div>
      )}

      {!error && count > 0 && (
        <div className="px-2 pb-3">
          {convItems.map((t) => (
            <TodoItem key={t.id} todo={t}
                      onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
          ))}
          {agentItems.length > 0 && (
            <>
              <div className="px-2 pt-2 pb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-accent">
                📌 Persistent
              </div>
              {agentItems.map((t) => (
                <TodoItem key={t.id} todo={t}
                          onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
              ))}
            </>
          )}
        </div>
      )}
    </aside>
  );
}
