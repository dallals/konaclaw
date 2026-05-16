import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { PortfolioWidget } from "./PortfolioWidget";

vi.mock("../api/portfolio", () => ({
  getSnapshot: vi.fn(),
  syncHoldings: vi.fn(),
}));

import { getSnapshot, syncHoldings } from "../api/portfolio";


const SAMPLE_SNAPSHOT = {
  cached_at: "2026-05-15T22:00:00+00:00",
  payload: {
    total_value: 4_524_912.12,
    total_gain: 2_361_309.12,
    total_day_change: 76_430.15,
    day_pct: 1.72,
    holdings: [
      { ticker: "AAPL", value: 1_340_070.57, day_change: 33_870.00, gain_pct: 107.51 },
      { ticker: "NVDA", value: 1_084_030.00, day_change: 17_898.72, gain_pct: 2607.64 },
      { ticker: "VOO",  value:   716_099.66, day_change:  8_688.26, gain_pct:   47.48 },
      { ticker: "TSLA", value:   500_000.00, day_change:  -5_000.00, gain_pct:  10.00 },
    ],
  },
  stale: false,
};


describe("PortfolioWidget", () => {
  beforeEach(() => {
    (getSnapshot as any).mockReset();
    (syncHoldings as any).mockReset();
  });

  it("renders loading state initially", () => {
    (getSnapshot as any).mockReturnValue(new Promise(() => {}));
    render(<PortfolioWidget />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders total value + day change on success", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    expect(screen.getByText(/\+\$76,430/)).toBeInTheDocument();
    expect(screen.getByText(/1\.72%/)).toBeInTheDocument();
  });

  it("renders top 3 movers ordered by abs(day_change)", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    const moversSection = screen.getByTestId("top-movers");
    expect(moversSection).toHaveTextContent("AAPL");
    expect(moversSection).toHaveTextContent("NVDA");
    expect(moversSection).toHaveTextContent("VOO");
  });

  it("refresh button forces a refresh=true call", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    (getSnapshot as any).mockClear();
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledWith(true));
  });

  it("sync button calls syncHoldings and refreshes the snapshot", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    (syncHoldings as any).mockResolvedValue({
      synced_at: "2026-05-16T22:30:00+00:00",
      user_email: "sammydallal@gmail.com",
      tickers: 17,
      total_basis: 2_416_980.77,
      file: "/workspace/holdings.json",
    });
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    (getSnapshot as any).mockClear();

    fireEvent.click(screen.getByRole("button", { name: /sync holdings from rplanner/i }));

    await waitFor(() => expect(syncHoldings).toHaveBeenCalled());
    // Sync triggers a snapshot refresh so the new holdings.json is reflected.
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledWith(true));
    // Status line confirms the sync ran.
    await waitFor(() => expect(screen.getByText(/synced 17 tickers/i)).toBeInTheDocument());
  });

  it("sync failure surfaces the error message", async () => {
    (getSnapshot as any).mockResolvedValue(SAMPLE_SNAPSHOT);
    (syncHoldings as any).mockRejectedValue(new Error("connection refused"));
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /sync holdings from rplanner/i }));

    await waitFor(() => expect(screen.getByText(/sync failed.*connection refused/i)).toBeInTheDocument());
  });

  it("renders error with last_good as stale", async () => {
    (getSnapshot as any).mockResolvedValue({
      cached_at: "2026-05-15T22:00:00+00:00",
      payload: null,
      stale: true,
      error: "yahoo down",
      last_good: SAMPLE_SNAPSHOT.payload,
    });
    render(<PortfolioWidget />);
    await waitFor(() => expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument());
    expect(screen.getByText(/yahoo down/i)).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-stale")).toBeInTheDocument();
  });
});
