import { describe, expect, it } from "vitest";

import { makeApp } from "../../core/app-model";

import { incidentApp, largeApp, quietApp } from "./layout-fixtures";
import {
  appTotals,
  edgeRows,
  fmtPct,
  fmtRps,
  healthForErrorRate,
  hotSpots,
  nodeStats,
  pushSeries,
  queueStats,
  worstHealth,
} from "./layout-metrics";

describe("layout metrics", () => {
  it("classifies error rates by the model's thresholds", () => {
    expect(healthForErrorRate(0.22, 100)).toBe("failing");
    expect(healthForErrorRate(0.02, 100)).toBe("degraded");
    expect(healthForErrorRate(0.001, 100)).toBe("ok");
    expect(healthForErrorRate(0, 0)).toBe("idle");
    expect(worstHealth("ok", "failing")).toBe("failing");
    expect(worstHealth("idle", "ok")).toBe("ok");
  });

  it("totals requests from the ingress edges and weights the error rate", () => {
    const s = makeApp("service");
    const t = appTotals(s);
    expect(t.rps).toBe(s.edges[0].rps);
    expect(t.errorRate).toBeCloseTo(s.edges[0].errorRate);
    expect(t.replicas).toEqual({ ready: 3, desired: 3 });
    expect(t.p50).toBe(s.nodes[1].p50);
  });

  it("marks a failing service and its missing replicas", () => {
    const t = appTotals(incidentApp("service"));
    expect(t.health).toBe("failing");
    expect(t.replicas.ready).toBeLessThan(t.replicas.desired);
  });

  it("reads a quiet app as idle with no traffic", () => {
    const t = appTotals(quietApp("service-data"));
    expect(t.rps).toBe(0);
    expect(t.health).toBe("idle");
  });

  it("worsens a node's health by the errors of calls into it", () => {
    const s = incidentApp("service-data");
    const db = s.nodes.find((n) => n.id === "db")!;
    const st = nodeStats(s, db);
    expect(st.errorRate).toBeCloseTo(0.22);
    expect(st.health).toBe("failing");
  });

  it("measures a queue backing up as enqueued minus consumed", () => {
    const q = queueStats(incidentApp("service-worker"))!;
    expect(q.inRps).toBe(420);
    expect(q.outRps).toBe(60);
    expect(q.net).toBe(360);
    expect(q.backingUp).toBe(true);
    expect(q.health).toBe("failing");
    expect(queueStats(makeApp("service"))).toBeNull();
  });

  it("ranks hot spots by error rate, failing first", () => {
    const hot = hotSpots(largeApp(), 5);
    expect(hot).toHaveLength(5);
    expect(hot[0].node.health).toBe("failing");
    for (let i = 1; i < hot.length; i++)
      expect(hot[i - 1].score).toBeGreaterThanOrEqual(hot[i].score);
    expect(hot.every((h) => h.node.role !== "ingress")).toBe(true);
  });

  it("sorts call paths by rps with shares of the busiest", () => {
    const rows = edgeRows(makeApp("microservices"));
    expect(rows[0].share).toBe(1);
    for (let i = 1; i < rows.length; i++)
      expect(rows[i - 1].edge.rps).toBeGreaterThanOrEqual(rows[i].edge.rps);
  });

  it("keeps a bounded rolling series", () => {
    let s: number[] = [];
    for (let i = 0; i < 40; i++) s = pushSeries(s, i, 30);
    expect(s).toHaveLength(30);
    expect(s[29]).toBe(39);
  });

  it("formats numbers compactly", () => {
    expect(fmtRps(1234)).toBe("1.2k");
    expect(fmtRps(42.4)).toBe("42");
    expect(fmtPct(0.004)).toBe("0.4%");
    expect(fmtPct(0.22)).toBe("22%");
  });
});
