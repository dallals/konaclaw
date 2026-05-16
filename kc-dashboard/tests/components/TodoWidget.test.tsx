import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { TodoWidget } from "../../src/components/TodoWidget";

beforeEach(() => {
  vi.restoreAllMocks();
  // Each test starts with no manual collapse preference so the widget
  // applies its default (auto-collapse when empty, expand when items exist).
  localStorage.removeItem("kc.todos.collapsed");
});

afterEach(() => {
  // Don't leak the collapse preference into sibling tests (Chat.test.tsx
  // renders TodoWidget too, and an "expanded" preference in jsdom can shift
  // layout-dependent queries).
  localStorage.removeItem("kc.todos.collapsed");
});

function mockFetchOnce(payload: any, status = 200) {
  vi.spyOn(globalThis, "fetch" as any).mockResolvedValueOnce({
    ok: status < 400,
    status,
    json: async () => payload,
  } as any);
}

describe("TodoWidget", () => {
  it("auto-collapses to a thin strip when no items, expands on click to show empty state", async () => {
    mockFetchOnce({ items: [], count: 0 });
    const { findByLabelText, findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    // Collapsed by default when empty — only the expand affordance is rendered.
    const expandBtn = await findByLabelText(/expand todos/i);
    expect(expandBtn).toBeTruthy();
    // Clicking expands the widget and reveals the empty-state message.
    fireEvent.click(expandBtn);
    expect(await findByText(/no todos yet/i)).toBeTruthy();
  });

  it("renders items returned from /todos", async () => {
    mockFetchOnce({
      items: [
        { id: 1, agent: "Kona-AI", conversation_id: 40, title: "Pack",
          notes: "", status: "open", scope: "conversation",
          created_at: 1, updated_at: 1 },
        { id: 2, agent: "Kona-AI", conversation_id: null, title: "Renew passport",
          notes: "", status: "open", scope: "agent",
          created_at: 2, updated_at: 2 },
      ],
      count: 2,
    });
    const { findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    expect(await findByText("Pack")).toBeTruthy();
    expect(await findByText("Renew passport")).toBeTruthy();
  });

  it("groups agent-scoped items under a 'Persistent' header", async () => {
    mockFetchOnce({
      items: [
        { id: 1, conversation_id: 40, title: "Pack", notes: "", status: "open",
          scope: "conversation", agent: "Kona-AI", created_at: 1, updated_at: 1 },
        { id: 2, conversation_id: null, title: "Renew", notes: "", status: "open",
          scope: "agent", agent: "Kona-AI", created_at: 2, updated_at: 2 },
      ],
      count: 2,
    });
    const { findByText } = render(
      <TodoWidget conversationId={40} agent="Kona-AI" />
    );
    expect(await findByText(/Persistent/)).toBeTruthy();
  });
});
