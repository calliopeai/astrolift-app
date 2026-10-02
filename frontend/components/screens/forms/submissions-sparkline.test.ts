import { describe, expect, it } from "vitest";

import { buildSubmissionsSparkline } from "./submissions-sparkline";

const submissions = (...dates: string[]) => dates.map((submittedAt) => ({ submittedAt }));

describe("submission calendar windows", () => {
  it("includes today's submissions east of UTC and uses the same zone at the oldest boundary", () => {
    const points = buildSubmissionsSparkline(
      submissions(
        "2026-01-02T14:59:59Z", // January 2 in Tokyo, outside the window.
        "2026-01-02T15:00:00Z", // January 3, the oldest included day.
        "2026-01-31T15:00:00Z", // February 1, today.
        "2026-02-01T15:00:00Z" // February 2, outside the window.
      ),
      "Asia/Tokyo",
      new Date("2026-01-31T15:30:00Z")
    );
    expect(points).toHaveLength(30);
    expect(points[0]).toEqual({ day: "2026-01-03", count: 1 });
    expect(points.at(-1)).toEqual({ day: "2026-02-01", count: 1 });
    expect(points.reduce((sum, point) => sum + point.count, 0)).toBe(2);
  });

  it("keeps the previous calendar day west of UTC and normalizes timestamp offsets", () => {
    const points = buildSubmissionsSparkline(
      submissions("2026-02-01T01:00:00Z", "2026-02-01T10:00:00+09:00"),
      "America/Los_Angeles",
      new Date("2026-02-01T02:00:00Z")
    );
    expect(points[0].day).toBe("2026-01-02");
    expect(points.at(-1)).toEqual({ day: "2026-01-31", count: 2 });
  });

  it.each([
    ["2026-03-09T01:00:00Z", "2026-02-07", "2026-03-08"],
    ["2026-11-02T01:00:00Z", "2026-10-03", "2026-11-01"],
  ])("retains 30 consecutive calendar labels across daylight saving at %s", (now, first, last) => {
    const points = buildSubmissionsSparkline([], "America/New_York", new Date(now));
    expect(points).toHaveLength(30);
    expect(new Set(points.map((point) => point.day)).size).toBe(30);
    expect(points[0].day).toBe(first);
    expect(points.at(-1)?.day).toBe(last);
    expect(points.every((point) => point.count === 0)).toBe(true);
  });

  it("ignores missing, invalid and out-of-window timestamps", () => {
    const points = buildSubmissionsSparkline(
      submissions("", "not-a-date", "2025-12-01T00:00:00Z", "2026-02-02T00:00:00Z"),
      "UTC",
      new Date("2026-02-01T12:00:00Z")
    );
    expect(points).toHaveLength(30);
    expect(points.every((point) => point.count === 0)).toBe(true);
  });
});
