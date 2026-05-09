import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewsWidget } from "../../src/components/NewsWidget";

function withQuery(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const okBody = {
  articles: [
    {
      title: "Story A",
      source: "BBC News",
      url: "https://example.com/a",
      published_at: "2026-05-08T10:00:00Z",
      snippet: "snip",
    },
  ],
  cached: false,
};

describe("NewsWidget", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("renders header and toggle", () => {
    render(withQuery(<NewsWidget />));
    expect(screen.getByText("News")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Topic/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Source/i })).toBeInTheDocument();
  });

  it("fetches and renders articles when user clicks Go", async () => {
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: true, status: 200, json: async () => okBody,
    });
    render(withQuery(<NewsWidget />));
    fireEvent.change(screen.getByPlaceholderText(/topic/i), { target: { value: "ai" } });
    fireEvent.click(screen.getByRole("button", { name: /^Go$/ }));
    await waitFor(() => expect(screen.getByText(/Story A/)).toBeInTheDocument());
    expect(screen.getByText(/BBC News/)).toBeInTheDocument();
  });

  it("renders quota_reached banner on 429", async () => {
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: false, status: 429,
      json: async () => ({ error: "quota_reached", message: "no quota" }),
    });
    render(withQuery(<NewsWidget />));
    fireEvent.change(screen.getByPlaceholderText(/topic/i), { target: { value: "ai" } });
    fireEvent.click(screen.getByRole("button", { name: /^Go$/ }));
    await waitFor(() => expect(screen.getByText(/Daily news quota reached/i)).toBeInTheDocument());
  });

  it("renders not_configured banner on 503", async () => {
    (globalThis.fetch as any).mockResolvedValueOnce({
      ok: false, status: 503,
      json: async () => ({ error: "not_configured", message: "x" }),
    });
    render(withQuery(<NewsWidget />));
    fireEvent.change(screen.getByPlaceholderText(/topic/i), { target: { value: "ai" } });
    fireEvent.click(screen.getByRole("button", { name: /^Go$/ }));
    await waitFor(() =>
      expect(screen.getByText(/News not configured/i)).toBeInTheDocument(),
    );
  });

  it("persists last query and mode to localStorage", async () => {
    (globalThis.fetch as any).mockResolvedValue({
      ok: true, status: 200, json: async () => okBody,
    });
    render(withQuery(<NewsWidget />));
    fireEvent.click(screen.getByRole("button", { name: /Source/i }));
    fireEvent.change(screen.getByPlaceholderText(/source/i), { target: { value: "bbc-news" } });
    fireEvent.click(screen.getByRole("button", { name: /^Go$/ }));
    await waitFor(() => {
      expect(localStorage.getItem("kc.news.mode")).toBe("source");
      expect(localStorage.getItem("kc.news.value")).toBe("bbc-news");
    });
  });

  it("rehydrates last query and mode from localStorage on mount", () => {
    localStorage.setItem("kc.news.mode", "source");
    localStorage.setItem("kc.news.value", "the-verge");
    render(withQuery(<NewsWidget />));
    const input = screen.getByPlaceholderText(/source/i) as HTMLInputElement;
    expect(input.value).toBe("the-verge");
  });
});
