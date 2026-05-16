import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { PortfolioWidget } from "./PortfolioWidget";
import type { PortfolioSnapshot } from "../api/portfolio";


const SAMPLE: PortfolioSnapshot = {
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
};


describe("PortfolioWidget", () => {
  it("renders total value + day change", () => {
    render(<PortfolioWidget snapshot={SAMPLE} />);
    expect(screen.getByText(/\$4,524,912/)).toBeInTheDocument();
    expect(screen.getByText(/\+\$76,430/)).toBeInTheDocument();
    expect(screen.getByText(/1\.72%/)).toBeInTheDocument();
  });

  it("renders top 3 movers ordered by abs(day_change)", () => {
    render(<PortfolioWidget snapshot={SAMPLE} />);
    const movers = screen.getByTestId("top-movers");
    expect(movers).toHaveTextContent("AAPL");
    expect(movers).toHaveTextContent("NVDA");
    expect(movers).toHaveTextContent("VOO");
    // TSLA's |day_change|=5k is below VOO's 8.7k, so it's excluded from top 3.
    expect(movers).not.toHaveTextContent("TSLA");
  });

  it("uses red color class when total day change is negative", () => {
    const negative: PortfolioSnapshot = { ...SAMPLE, total_day_change: -1234, day_pct: -0.03 };
    const { container } = render(<PortfolioWidget snapshot={negative} />);
    expect(container.querySelector(".text-red-600")).not.toBeNull();
  });
});
