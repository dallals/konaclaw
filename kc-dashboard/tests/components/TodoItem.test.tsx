import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { TodoItem } from "../../src/components/TodoItem";

const sample = {
  id: 1, agent: "Kona-AI", conversation_id: 40, title: "Pack",
  notes: "warm clothes", status: "open" as const, scope: "conversation" as const,
  created_at: 1, updated_at: 1,
};

describe("TodoItem", () => {
  it("renders title and notes", () => {
    const { getByText } = render(
      <TodoItem todo={sample} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    expect(getByText("Pack")).toBeTruthy();
    expect(getByText(/warm clothes/)).toBeTruthy();
  });

  it("shows pin icon for agent-scoped items", () => {
    const persistent = { ...sample, scope: "agent" as const, conversation_id: null };
    const { container } = render(
      <TodoItem todo={persistent} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    expect(container.textContent).toContain("📌");
  });

  it("checkbox click fires onToggle with new status", () => {
    const onToggle = vi.fn();
    const { container } = render(
      <TodoItem todo={sample} onToggle={onToggle} onEdit={vi.fn()} onDelete={vi.fn()} />
    );
    const cb = container.querySelector("input[type=checkbox]") as HTMLInputElement;
    fireEvent.click(cb);
    expect(onToggle).toHaveBeenCalledWith(1, "done");
  });

  it("delete button fires onDelete", () => {
    const onDelete = vi.fn();
    const { getByLabelText } = render(
      <TodoItem todo={sample} onToggle={vi.fn()} onEdit={vi.fn()} onDelete={onDelete} />
    );
    fireEvent.click(getByLabelText(/delete/i));
    expect(onDelete).toHaveBeenCalledWith(1);
  });
});
