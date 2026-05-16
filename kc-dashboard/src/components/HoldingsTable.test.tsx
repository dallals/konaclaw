import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { HoldingsTable } from "./HoldingsTable";
import type { PortfolioHolding } from "../api/portfolio";

const ROWS: PortfolioHolding[] = [
  { ticker: "AAPL", shares: 4894, price: 220.55, value: 1_080_000, day_change: +12_000, gain: 800_000, gain_pct: 90 },
  { ticker: "VOO",  shares: 1869, price: 679.44, value: 1_270_000, day_change: -15_000, gain: 600_000, gain_pct: 80 },
  { ticker: "NVDA", shares: 5374, price:  20.10, value:   108_000, day_change:    +300, gain:  60_000, gain_pct: 150 },
];

describe("HoldingsTable", () => {
  it("renders one row per holding with ticker + value", () => {
    render(<HoldingsTable holdings={ROWS} />);
    for (const r of ROWS) {
      expect(screen.getByText(r.ticker)).toBeInTheDocument();
    }
  });

  it("sorts by value desc by default", () => {
    render(<HoldingsTable holdings={ROWS} />);
    const rows = screen.getAllByRole("row").slice(1); // skip header
    const firstCell = within(rows[0]).getAllByRole("cell")[0];
    expect(firstCell.textContent).toBe("VOO"); // 1.27M > 1.08M > 108k
  });

  it("clicking the Ticker header sorts alphabetically", () => {
    render(<HoldingsTable holdings={ROWS} />);
    fireEvent.click(screen.getByText(/Ticker/));
    const rows = screen.getAllByRole("row").slice(1);
    const firstCell = within(rows[0]).getAllByRole("cell")[0];
    expect(firstCell.textContent).toBe("AAPL");
  });

  it("clicking the active header again flips direction", () => {
    render(<HoldingsTable holdings={ROWS} />);
    fireEvent.click(screen.getByText(/Ticker/)); // asc → A..N..V
    fireEvent.click(screen.getByText(/Ticker/)); // desc → V..N..A
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getAllByRole("cell")[0].textContent).toBe("VOO");
  });

  it("renders empty state when given no holdings", () => {
    render(<HoldingsTable holdings={[]} />);
    expect(screen.getByText(/no holdings/i)).toBeInTheDocument();
  });
});
