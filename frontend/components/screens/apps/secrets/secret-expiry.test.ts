import { describe, expect, it } from "vitest";
import { secretExpiry } from "./secret-expiry";

const NOW = Date.parse("2026-10-10T15:00:00Z");
const at = (hours: number) => new Date(NOW + hours * 3600_000).toISOString();

describe("secretExpiry", () => {
  it("reports a key that lapsed hours ago as expired today, not a day ago", () => {
    expect(secretExpiry(at(-3), NOW)).toEqual({ kind: "expiredToday" });
  });
  it("counts whole days since expiry", () => {
    expect(secretExpiry(at(-25), NOW)).toEqual({ kind: "expired", days: 1 });
    expect(secretExpiry(at(-24 * 3 - 1), NOW)).toEqual({ kind: "expired", days: 3 });
  });
  it("rounds remaining time up so a key expiring later today has one day left", () => {
    expect(secretExpiry(at(2), NOW)).toEqual({ kind: "remaining", days: 1 });
    expect(secretExpiry(at(24 * 7), NOW)).toEqual({ kind: "remaining", days: 7 });
    expect(secretExpiry(at(24 * 7 + 1), NOW)).toEqual({ kind: "remaining", days: 8 });
  });
  it("ignores unparseable values", () => {
    expect(secretExpiry("not-a-date", NOW)).toBeNull();
  });
});
