import { describe, expect, it } from "vitest";

import { EVENTS } from "@/components/screens/alerts/alerts.fixtures";

import {
  alertsFiringFirst,
  clustersWorstFirst,
  healthCounts,
  kpiFigures,
  platformRunReason,
  spendMeter,
} from "./builder-operator-model";
import {
  AGENT_RUNS,
  BUDGET,
  BUDGET_NEAR,
  BUDGET_OVER,
  CLUSTERS,
  FORECAST,
  METRICS,
  NOW,
} from "./builder-operator.fixtures";

describe("alertsFiringFirst", () => {
  it("puts firing before acknowledged before resolved, then the worst severity", () => {
    const [critical, ackedWarn, resolvedInfo] = EVENTS;
    const firingInfo = { ...resolvedInfo!, id: "fi", resolvedAt: null };
    const order = alertsFiringFirst([resolvedInfo!, ackedWarn!, firingInfo, critical!]);
    expect(order.map((e) => e.id)).toEqual([critical!.id, "fi", ackedWarn!.id, resolvedInfo!.id]);
  });
});

describe("clusters", () => {
  it("ranks a failed setup, then offline, degraded, never seen, connected", () => {
    expect(clustersWorstFirst(CLUSTERS).map((c) => c.name)).toEqual([
      "edge-sao",
      "staging-eu",
      "prod-eu",
      "new-gke",
      "dev",
      "prod-us",
    ]);
  });

  it("counts each health", () => {
    expect(healthCounts(CLUSTERS)).toEqual({
      error: 1,
      offline: 1,
      degraded: 1,
      never_seen: 1,
      connected: 2,
      unknown: 0,
    });
  });

  it("counts unknown heartbeat tokens without treating them as known agent absence", () => {
    for (const heartbeatStatus of ["LITERAL_FUTURE_STATUS", "__proto__", "constructor", ""]) {
      const row = { ...CLUSTERS[0]!, lifecycle: "managed", heartbeatStatus };
      const counts = healthCounts([row]);
      expect(counts.unknown).toBe(1);
      expect(counts.never_seen).toBe(0);
      expect(counts.connected).toBe(0);
      expect(Object.values(counts).reduce((total, n) => total + n, 0)).toBe(1);
      expect(clustersWorstFirst([row, ...CLUSTERS]).findIndex((cluster) => cluster === row)).toBe(
        3
      );
    }
  });
});

describe("kpiFigures", () => {
  const all = kpiFigures({
    metrics: METRICS,
    runs: { runs: AGENT_RUNS, capped: false, now: NOW },
    forecast: FORECAST,
    windowDays: 30,
  });

  it("is one row: deploys, runs, success, p95, spend", () => {
    expect(all.map((f) => [f.key, f.value])).toEqual([
      ["deploys", "14"],
      ["runs", "5"],
      ["success", "92.9%"],
      ["p95", "3m 10s"],
      ["spend", "$1,240"],
    ]);
  });

  it("leaves out what the person may not see", () => {
    const agentsOnly = kpiFigures({
      runs: { runs: AGENT_RUNS, capped: false, now: NOW },
      windowDays: 30,
    });
    expect(agentsOnly.map((f) => f.key)).toEqual(["runs"]);
  });

  it("counts only runs in the period, and says a full window is a floor", () => {
    const old = { ...AGENT_RUNS[0]!, startedAt: new Date(NOW - 40 * 864e5).toISOString() };
    const partial = kpiFigures({
      runs: { runs: [...AGENT_RUNS, old], capped: true, now: NOW },
      windowDays: 30,
    });
    expect(partial[0]!.value).toBe("5");
    const full = kpiFigures({ runs: { runs: AGENT_RUNS, capped: true, now: NOW }, windowDays: 30 });
    expect(full[0]!.value).toBe("5+");
  });

  it("draws a dash before a source answers, and when there were no deploys", () => {
    const pending = kpiFigures({ metrics: null, forecast: null, windowDays: 30 });
    expect(pending.every((f) => f.value === null)).toBe(true);
    const none = kpiFigures({
      metrics: { ...METRICS, total: 0, p95DurationSeconds: null },
      windowDays: 30,
    });
    expect(none.find((f) => f.key === "success")!.value).toBe("—");
    expect(none.find((f) => f.key === "p95")!.value).toBe("—");
  });
});

describe("spendMeter", () => {
  it("draws spend and the projection against the budget", () => {
    const m = spendMeter(FORECAST, BUDGET)!;
    expect(m.spent).toBeCloseTo(0.62);
    expect(m.projected).toBeCloseTo(0.955);
    expect(m.tone).toBe("ok");
  });

  it("warns past the first alert threshold and errs over the budget", () => {
    expect(spendMeter(FORECAST, BUDGET_NEAR)!.tone).toBe("warn");
    const over = spendMeter(FORECAST, BUDGET_OVER)!;
    expect(over.tone).toBe("error");
    expect(over.spent).toBe(1);
    expect(over.ratio).toBeCloseTo(1.24);
  });

  it("has nothing to draw without a budget", () => {
    expect(spendMeter(FORECAST, null)).toBeNull();
  });
});

describe("platformRunReason", () => {
  it("reads the failure message's first line, and nothing for a run that did not fail", () => {
    expect(
      platformRunReason({ status: "failed", failure: { message: "ImagePullBackOff\nstack" } })
    ).toBe("ImagePullBackOff");
    expect(platformRunReason({ status: "failed", failure: {} })).toBe("No reason was recorded.");
    expect(platformRunReason({ status: "completed", failure: {} })).toBeNull();
  });
});
