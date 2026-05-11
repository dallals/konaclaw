import type { Todo } from "../api/todos";

export type TodoItemProps = {
  todo: Todo;
  onToggle: (id: number, newStatus: "open" | "done") => void;
  onEdit:   (id: number) => void;
  onDelete: (id: number) => void;
};

export function TodoItem({ todo, onToggle, onEdit, onDelete }: TodoItemProps) {
  const isDone = todo.status === "done";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: "4px 6px",
        borderRadius: 3,
        opacity: isDone ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={isDone}
        onChange={() => onToggle(todo.id, isDone ? "open" : "done")}
        aria-label={`toggle ${todo.title}`}
      />
      <div
        style={{ flex: 1, cursor: "pointer" }}
        onClick={() => onEdit(todo.id)}
      >
        <div style={{
          fontSize: 12,
          fontWeight: 500,
          textDecoration: isDone ? "line-through" : "none",
        }}>
          {todo.scope === "agent" && <span title="persistent" style={{ marginRight: 4 }}>📌</span>}
          {todo.title}
        </div>
        {todo.notes && (
          <div style={{ fontSize: 10, color: "#888", marginTop: 2 }}>
            {todo.notes.split("\n")[0].slice(0, 80)}
          </div>
        )}
      </div>
      <button
        aria-label={`delete ${todo.title}`}
        onClick={() => onDelete(todo.id)}
        style={{
          background: "transparent",
          border: "none",
          color: "#888",
          cursor: "pointer",
          padding: 0,
        }}
        title="Delete"
      >
        ×
      </button>
    </div>
  );
}
