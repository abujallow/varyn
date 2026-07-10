import { describe, expect, it } from "vitest";
import { backendLabel, sourceHealthTitle, sourceStatusLabel } from "../systemHealth.js";

describe("sourceStatusLabel", () => {
  it("returns Awaiting for a missing source", () => {
    expect(sourceStatusLabel(null)).toBe("Awaiting");
    expect(sourceStatusLabel(undefined)).toBe("Awaiting");
  });

  it("returns Disabled when the source is explicitly disabled", () => {
    expect(sourceStatusLabel({ enabled: false, status: "active" })).toBe("Disabled");
  });

  it("maps known status values case-insensitively", () => {
    expect(sourceStatusLabel({ status: "active" })).toBe("Active");
    expect(sourceStatusLabel({ status: "ACTIVE" })).toBe("Active");
    expect(sourceStatusLabel({ status: "degraded" })).toBe("Degraded");
    expect(sourceStatusLabel({ status: "unavailable" })).toBe("Unavailable");
  });

  it("falls back to Awaiting for unknown or missing status", () => {
    expect(sourceStatusLabel({})).toBe("Awaiting");
    expect(sourceStatusLabel({ status: "something-else" })).toBe("Awaiting");
  });
});

describe("sourceHealthTitle", () => {
  it("returns an awaiting message for a missing source", () => {
    expect(sourceHealthTitle("yfinance", null)).toBe("yfinance: awaiting first source check");
  });

  it("includes the label and status", () => {
    const title = sourceHealthTitle("SEC EDGAR", { status: "active" });
    expect(title).toContain("SEC EDGAR: Active");
  });

  it("includes the last check time when a successful pull is recorded", () => {
    const title = sourceHealthTitle("FRED", {
      status: "active",
      last_successful_pull: "2026-01-05T10:00:00Z",
    });
    expect(title).toMatch(/last checked/);
  });

  it("prefers last_failed_pull over last_successful_pull for the check time", () => {
    const title = sourceHealthTitle("CFPB", {
      status: "degraded",
      last_successful_pull: "2026-01-01T00:00:00Z",
      last_failed_pull: "2026-01-05T10:00:00Z",
    });
    expect(title).toMatch(/last checked/);
  });

  it("appends the last error message when present", () => {
    const title = sourceHealthTitle("yfinance", {
      status: "unavailable",
      last_error: "rate limited",
    });
    expect(title).toContain("rate limited");
  });

  it("omits absent details instead of leaving empty segments", () => {
    const title = sourceHealthTitle("FRED", { status: "active" });
    expect(title).toBe("FRED: Active");
  });
});

describe("backendLabel", () => {
  it("labels the hosted deployment without reporting a local port", () => {
    expect(backendLabel({ hosted: true, backendPort: 8788 })).toBe("Hosted Agent");
  });

  it("labels a hosted deployment even when no port is reported", () => {
    expect(backendLabel({ hosted: true, backendPort: undefined })).toBe("Hosted Agent");
  });

  it("labels local development with its port when not hosted", () => {
    expect(backendLabel({ hosted: false, backendPort: 8788 })).toBe("Local Agent 8788");
  });

  it("prefers the hosted label over the port when both signals are present", () => {
    expect(backendLabel({ hosted: true, backendPort: 8788 })).not.toContain("8788");
  });

  it("returns null when neither signal is available yet", () => {
    expect(backendLabel({})).toBeNull();
    expect(backendLabel()).toBeNull();
  });
});
