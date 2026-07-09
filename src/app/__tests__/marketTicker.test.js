import { describe, expect, it } from "vitest";
import {
  buildMarketTickerItems,
  formatMarketChange,
  formatMarketPrice,
  formatMarketTimestamp,
  isTickerAvailable,
} from "../marketTicker.js";

describe("formatMarketPrice", () => {
  it("formats a normal price with two decimals", () => {
    expect(formatMarketPrice(285.4)).toBe("$285.40");
  });

  it("renders zero as a real price, not Unavailable", () => {
    expect(formatMarketPrice(0)).toBe("$0.00");
  });

  it("returns Unavailable for null/undefined/NaN", () => {
    expect(formatMarketPrice(null)).toBe("Unavailable");
    expect(formatMarketPrice(undefined)).toBe("Unavailable");
    expect(formatMarketPrice(Number.NaN)).toBe("Unavailable");
  });

  it("returns Unavailable for a non-numeric string", () => {
    expect(formatMarketPrice("")).toBe("Unavailable");
    expect(formatMarketPrice("N/A")).toBe("Unavailable");
  });
});

describe("formatMarketChange", () => {
  it("formats a positive change with a leading plus sign", () => {
    expect(formatMarketChange(1.5)).toBe("+1.50%");
  });

  it("formats a negative change without double sign", () => {
    expect(formatMarketChange(-2.34)).toBe("-2.34%");
  });

  it("renders zero as a real flat move, not a dash", () => {
    expect(formatMarketChange(0)).toBe("+0.00%");
  });

  it("returns -- for missing values", () => {
    expect(formatMarketChange(null)).toBe("--");
    expect(formatMarketChange(undefined)).toBe("--");
    expect(formatMarketChange("")).toBe("--");
  });
});

describe("isTickerAvailable", () => {
  it("is true only when the backend explicitly marks the item available", () => {
    expect(isTickerAvailable({ available: true, price: 100 })).toBe(true);
  });

  it("is false when available is false, even with valid-looking numeric fields", () => {
    expect(isTickerAvailable({ available: false, price: 100, change_percent: 1 })).toBe(false);
  });

  it("does not derive availability from price being zero", () => {
    // A price of 0 must not be conflated with "unavailable" -- availability comes
    // strictly from the backend's own available flag, never a truthiness check.
    expect(isTickerAvailable({ available: true, price: 0 })).toBe(true);
  });

  it("is false for missing/malformed items", () => {
    expect(isTickerAvailable(null)).toBe(false);
    expect(isTickerAvailable(undefined)).toBe(false);
    expect(isTickerAvailable({})).toBe(false);
  });
});

describe("buildMarketTickerItems", () => {
  const watchlist = ["TSLA", "F", "GM", "NVDA", "JPM", "BAC", "MTB"];

  it("renders real snapshot data when present", () => {
    const snapshot = [
      { symbol: "TSLA", available: true, price: 250.1, change_percent: 1.2 },
      { symbol: "GM", available: true, price: 40.5, change_percent: -0.5 },
    ];
    const items = buildMarketTickerItems(snapshot, watchlist);
    expect(items).toHaveLength(2);
    expect(items[0].symbol).toBe("TSLA");
    expect(isTickerAvailable(items[0])).toBe(true);
  });

  it("falls back to unavailable placeholders per watchlist symbol when snapshot is empty", () => {
    const items = buildMarketTickerItems([], watchlist);
    expect(items).toHaveLength(watchlist.length);
    for (const item of items) {
      expect(isTickerAvailable(item)).toBe(false);
      expect(item.stale).toBe(true);
    }
  });

  it("still renders every symbol when only some are available (partial outage)", () => {
    const snapshot = [
      { symbol: "TSLA", available: true, price: 250.1, change_percent: 1.2 },
      { symbol: "F", available: false },
      { symbol: "GM", available: true, price: 40.5, change_percent: 0 },
      { symbol: "NVDA", available: false },
      { symbol: "JPM", available: true, price: 285.4, change_percent: 0.9 },
      { symbol: "BAC", available: false },
      { symbol: "MTB", available: true, price: 180.2, change_percent: -1.1 },
    ];
    const items = buildMarketTickerItems(snapshot, watchlist);
    expect(items).toHaveLength(7);
    const bySymbol = Object.fromEntries(items.map((item) => [item.symbol, item]));
    expect(isTickerAvailable(bySymbol.TSLA)).toBe(true);
    expect(isTickerAvailable(bySymbol.F)).toBe(false);
    expect(isTickerAvailable(bySymbol.GM)).toBe(true);
    expect(bySymbol.GM.change_percent).toBe(0);
  });

  it("deduplicates repeated symbols, keeping the first occurrence", () => {
    const snapshot = [
      { symbol: "TSLA", available: true, price: 250.1 },
      { symbol: "TSLA", available: false },
    ];
    const items = buildMarketTickerItems(snapshot, watchlist);
    expect(items).toHaveLength(1);
    expect(isTickerAvailable(items[0])).toBe(true);
  });
});

describe("formatMarketTimestamp", () => {
  it("returns null for missing input", () => {
    expect(formatMarketTimestamp(null)).toBeNull();
    expect(formatMarketTimestamp(undefined)).toBeNull();
  });

  it("returns null for an unparseable date", () => {
    expect(formatMarketTimestamp("not-a-date")).toBeNull();
  });

  it("formats a valid ISO timestamp", () => {
    const formatted = formatMarketTimestamp("2026-01-05T10:00:00Z");
    expect(typeof formatted).toBe("string");
    expect(formatted.length).toBeGreaterThan(0);
  });
});
