import { describe, it, expect } from "vitest";
import {
  formatTokensPerSecond,
  formatTokenCount,
  formatTtfb,
} from "../../src/lib/formatUsage";

describe("formatTokensPerSecond", () => {
  it("integer when >= 10", () => {
    expect(formatTokensPerSecond(127)).toBe("127 t/s");
    expect(formatTokensPerSecond(10)).toBe("10 t/s");
  });
  it("one decimal when < 10", () => {
    expect(formatTokensPerSecond(8.43)).toBe("8.4 t/s");
    expect(formatTokensPerSecond(0.5)).toBe("0.5 t/s");
  });
});

describe("formatTokenCount", () => {
  it("integer under 10000", () => {
    expect(formatTokenCount(412)).toBe("412 tok");
    expect(formatTokenCount(9999)).toBe("9999 tok");
  });
  it("k suffix at and above 10000", () => {
    expect(formatTokenCount(10000)).toBe("10.0k tok");
    expect(formatTokenCount(12400)).toBe("12.4k tok");
  });
});

describe("formatTtfb", () => {
  it("two decimals under 10s", () => {
    expect(formatTtfb(1042)).toBe("1.04 s");
    expect(formatTtfb(50)).toBe("0.05 s");
  });
  it("one decimal at and above 10s", () => {
    expect(formatTtfb(10000)).toBe("10.0 s");
    expect(formatTtfb(12345)).toBe("12.3 s");
  });
});
