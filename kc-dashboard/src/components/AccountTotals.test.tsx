import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AccountTotals } from "./AccountTotals";

const TOTALS = {
  Taxable:     { value: 4_233_089, basis: 1_734_827, gain: 2_498_262 },
  Traditional: { value:   644_383, basis:   451_307, gain:   193_076 },
  Roth:        { value:   172_641, basis:    75_930, gain:    96_711 },
};

describe("AccountTotals", () => {
  it("renders one card per account in stable Taxable/Traditional/Roth order", () => {
    render(<AccountTotals totals={TOTALS} />);
    const labels = screen.getAllByText(/Taxable|Traditional|Roth/);
    // Each appears twice when ordering check is loose; tighten to just the
    // account-name labels by filtering to the .uppercase font-mono class.
    const names = labels
      .filter((el) => el.className.includes("uppercase"))
      .map((el) => el.textContent);
    expect(names).toEqual(["Taxable", "Traditional", "Roth"]);
  });

  it("shows each account's value and percent of total", () => {
    render(<AccountTotals totals={TOTALS} />);
    expect(screen.getByText(/\$4,233,089/)).toBeInTheDocument();
    expect(screen.getByText(/\$644,383/)).toBeInTheDocument();
    expect(screen.getByText(/\$172,641/)).toBeInTheDocument();
    // Taxable share: 4.23M / 5.05M ≈ 83.8%
    expect(screen.getByText(/83\.8% of total/)).toBeInTheDocument();
  });

  it("renders nothing when given empty totals (pre-sync)", () => {
    const { container } = render(<AccountTotals totals={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("appends unknown account types after the canonical three", () => {
    render(
      <AccountTotals totals={{ ...TOTALS, "HSA": { value: 10_000, basis: 5_000, gain: 5_000 } }} />
    );
    const names = screen
      .getAllByText(/Taxable|Traditional|Roth|HSA/)
      .filter((el) => el.className.includes("uppercase"))
      .map((el) => el.textContent);
    expect(names).toEqual(["Taxable", "Traditional", "Roth", "HSA"]);
  });
});
