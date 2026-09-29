import { describe, expect, it } from "vitest";

import { makeApp, type AppSnapshot } from "../core/app-model";

import {
  EVENT_WINDOW_MS,
  cubicPoints,
  decodePts,
  describeApp,
  edgeHealth,
  edgeRate,
  encodePts,
  flashEvent,
  isoColumn,
  layoutAppGraph,
  layoutAppIso,
  orderLayers,
  particleCount,
  pointAlong,
  recency,
  recentEvents,
  truncate,
} from "./app-render-layout";
import { twentyNodes } from "./app-story-fixtures";

describe("edges", () => {
  it("fail over 5% errors and idle with no traffic", () => {
    expect(edgeHealth({ from: "a", to: "b", rps: 10, errorRate: 0.06 })).toBe("failing");
    expect(edgeHealth({ from: "a", to: "b", rps: 10, errorRate: 0.05 })).toBe("ok");
    expect(edgeHealth({ from: "a", to: "b", rps: 0, errorRate: 0 })).toBe("idle");
  });
  it("rate grows with rps and saturates", () => {
    expect(edgeRate(0)).toBe(0);
    expect(edgeRate(10)).toBeLessThan(edgeRate(300));
    expect(edgeRate(100_000)).toBe(1);
    expect(particleCount(0)).toBe(0);
    expect(particleCount(1)).toBe(4);
  });
});

describe("orderLayers", () => {
  it("puts callers left, runners middle, data right, and collapses empty layers", () => {
    const layers = orderLayers(makeApp("microservices"));
    expect(layers.map((l) => l.map((n) => n.role)[0])).toEqual(["ingress", "service", "data"]);
    const task = orderLayers(makeApp("task"));
    expect(task).toHaveLength(2);
  });
  it("orders a layer by its neighbours in the layer before", () => {
    const s = makeApp("microservices");
    const [, mid, right] = orderLayers(s);
    const midIds = mid.map((n) => n.id);
    // billing (called by orders) sits after orders; Stripe (called by billing) sits last.
    expect(midIds.indexOf("billing")).toBeGreaterThan(midIds.indexOf("orders"));
    expect(right.at(-1)?.id).toBe("stripe");
  });
});

describe("events", () => {
  const base = makeApp("functions");
  const s: AppSnapshot = {
    ...base,
    events: [
      { id: "a", kind: "invoked", nodeId: "resize", at: base.now - 5000 },
      { id: "b", kind: "invoked", nodeId: "resize", at: base.now - 500 },
      { id: "c", kind: "invoked", nodeId: "resize", at: base.now - EVENT_WINDOW_MS - 1 },
      { id: "d", kind: "agent_action", nodeId: "resize", at: base.now },
    ],
  };
  it("keeps only the window, newest last", () => {
    expect(recentEvents(s, "resize", "invoked").map((e) => e.id)).toEqual(["a", "b"]);
  });
  it("flashes only a fresh event and fades by age", () => {
    expect(flashEvent(s, "resize", "invoked")?.id).toBe("b");
    const old = { ...s, now: s.now + 3000 };
    expect(flashEvent(old, "resize", "invoked")).toBeUndefined();
    expect(recency(s, s.events[1])).toBeCloseTo(1 - 500 / EVENT_WINDOW_MS);
    expect(recency(s, undefined)).toBe(0);
  });
});

describe("geometry", () => {
  it("walks a polyline by length", () => {
    const pts: [number, number][] = [
      [0, 0],
      [10, 0],
      [10, 10],
    ];
    expect(pointAlong(pts, 0)).toEqual([0, 0]);
    expect(pointAlong(pts, 0.5)).toEqual([10, 0]);
    expect(pointAlong(pts, 0.75)).toEqual([10, 5]);
    expect(pointAlong(pts, 1)).toEqual([10, 10]);
  });
  it("samples a cubic through its ends and round-trips encoding", () => {
    const c = cubicPoints([0, 0], [5, 0], [5, 10], [10, 10], 8);
    expect(c[0]).toEqual([0, 0]);
    expect(c[8]).toEqual([10, 10]);
    expect(decodePts(encodePts([[1.25, 2]]))).toEqual([[1.3, 2]]);
  });
});

describe("layouts", () => {
  it("draws every edge and node for 2 to 20 nodes", () => {
    for (const s of [makeApp("task"), makeApp("microservices"), twentyNodes()]) {
      const g = layoutAppGraph(s);
      const i = layoutAppIso(s);
      expect(g.nodes).toHaveLength(s.nodes.length);
      expect(i.nodes).toHaveLength(s.nodes.length);
      expect(g.edges).toHaveLength(s.edges.length);
      expect(i.edges).toHaveLength(s.edges.length);
      expect(i.viewBox.every(Number.isFinite)).toBe(true);
    }
  });
  it("puts externals on the far rim of the platform", () => {
    expect(isoColumn("external", new Set([0, 1, 2, 3]))).toBe(3);
    expect(isoColumn("external", new Set([1, 3]))).toBe(1);
    const i = layoutAppIso(makeApp("microservices"));
    const stripe = i.nodes.find((n) => n.node.id === "stripe")!;
    expect(Math.max(...i.nodes.map((n) => n.cx))).toBe(stripe.cx);
  });
  it("keeps the iso frame stable as load changes", () => {
    const s = makeApp("microservices");
    const busy = { ...s, nodes: s.nodes.map((n) => ({ ...n, load: 1 })) };
    expect(layoutAppIso(busy).viewBox).toEqual(layoutAppIso(s).viewBox);
  });
});

describe("summaries", () => {
  it("counts failing, busy and erroring connections", () => {
    const s = makeApp("microservices", { incident: true, name: "shop" });
    const text = describeApp(s);
    expect(text).toMatch(/^shop: 9 nodes, 1 failing/);
    expect(text).toMatch(/1 of 10 connections erroring/);
    expect(truncate("abcdefgh", 5)).toBe("abcd…");
    expect(truncate("abc", 5)).toBe("abc");
  });
});
