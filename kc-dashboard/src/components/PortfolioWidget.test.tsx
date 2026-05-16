import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { PortfolioWidget } from "./PortfolioWidget";

vi.mock("../api/portfolio", () => ({
  getSnapshot: vi.fn(),
}));

import { getSnapshot } from "../api/portfolio";


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
