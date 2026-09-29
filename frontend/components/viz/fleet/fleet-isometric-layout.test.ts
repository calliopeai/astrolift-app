import { describe, expect, it } from "vitest";

import { makeFleet, stepFleet } from "../core/fleet-model";
import { depth } from "../core/iso";
import { mulberry32 } from "../core/semantics";

import {
  IDLE_HEIGHT,
  MAX_HEIGHT,
  MIN_HEIGHT,
  boxHeight,
  fleetAriaLabel,
  fleetBounds,
  layoutFleet,
  recentStarts,
  summarizeFleet,
  truncate,
} from "./fleet-isometric-layout";

describe("boxHeight", () => {
  it("keeps idle agents low whatever their load", () => {
    expect(boxHeight({ health: "idle", load: 0.9 })).toBe(IDLE_HEIGHT);
  });
  it("maps load onto height and clamps it", () => {
    expect(boxHeight({ health: "ok", load: 0 })).toBe(MIN_HEIGHT);
    expect(boxHeight({ health: "ok", load: 1 })).toBe(MAX_HEIGHT);
    expect(boxHeight({ health: "failing", load: 2 })).toBe(MAX_HEIGHT);
    expect(boxHeight({ health: "ok", load: 0.7 })).toBeGreaterThan(
      boxHeight({ health: "ok", load: 0.3 })
    );
  });
});

describe("layoutFleet", () => {
  it("puts every agent on its own cluster's platform, far first", () => {
    const fleet = makeFleet({ clusters: 4, agentsPerCluster: 7 });
    const platforms = layoutFleet(fleet);
    expect(platforms).toHaveLength(4);
    expect(platforms.flatMap((p) => p.boxes)).toHaveLength(28);
    for (const p of platforms) {
      for (const b of p.boxes) {
        expect(b.agent.clusterId).toBe(p.cluster.id);
        expect(b.x).toBeGreaterThanOrEqual(p.x);
        expect(b.y).toBeGreaterThanOrEqual(p.y);
        expect(b.x).toBeLessThan(p.x + p.w);
        expect(b.y).toBeLessThan(p.y + p.d);
      }
      const depths = p.boxes.map((b) => depth(b.x, b.y));
      expect(depths).toEqual([...depths].sort((a, b) => a - b));
    }
    const pd = platforms.map((p) => depth(p.x, p.y));
    expect(pd).toEqual([...pd].sort((a, b) => a - b));
  });

  it("gives no two agents the same cell", () => {
    const platforms = layoutFleet(makeFleet({ clusters: 6, agentsPerCluster: 36 }));
    const cells = new Set(platforms.flatMap((p) => p.boxes.map((b) => `${b.x},${b.y}`)));
    expect(cells.size).toBe(216);
  });

  it("does not move boxes when only load changes", () => {
    const fleet = makeFleet({ clusters: 3, agentsPerCluster: 5 });
    const next = stepFleet(fleet, mulberry32(2), 1200);
    const pos = (s: typeof fleet) =>
      layoutFleet(s).flatMap((p) => p.boxes.map((b) => [b.agent.id, b.x, b.y]));
    expect(pos(next)).toEqual(pos(fleet));
  });
});

describe("fleetBounds", () => {
  it("is positive and room-sized for an empty or full fleet", () => {
    expect(fleetBounds([], 22)).toEqual({ minX: 0, minY: 0, width: 1, height: 1 });
    const b = fleetBounds(layoutFleet(makeFleet()), 22);
    expect(b.width).toBeGreaterThan(0);
    expect(b.height).toBeGreaterThan(0);
  });
});

describe("summary", () => {
  it("counts the states the aria-label reports", () => {
    const fleet = makeFleet({ clusters: 2, agentsPerCluster: 3 });
    const agents = fleet.agents.map((a, i) => ({
      ...a,
      health: (["failing", "degraded", "idle", "ok", "ok", "ok"] as const)[i],
      activeRuns: i >= 3 ? 1 : 0,
    }));
    const s = summarizeFleet({ ...fleet, agents });
    expect(s).toEqual({ agents: 6, clusters: 2, failing: 1, degraded: 1, busy: 3, idle: 1 });
    expect(fleetAriaLabel(s)).toBe(
      "Isometric fleet: 6 agents across 2 clusters, 1 failing, 1 degraded, 3 busy, 1 idle"
    );
  });
});

describe("truncate", () => {
  it("leaves short text and ellipsizes long text to the limit", () => {
    expect(truncate("prod-east", 18)).toBe("prod-east");
    const cut = truncate("prod-east-customer-dedicated", 10);
    expect(cut).toHaveLength(10);
    expect(cut.endsWith("…")).toBe(true);
  });
});

describe("recentStarts", () => {
  it("keeps only run_started events inside the window", () => {
    const now = 100_000;
    const got = recentStarts(
      {
        now,
        events: [
          { id: "e1", kind: "run_started", agentId: "a1", at: now - 5000 },
          { id: "e2", kind: "dispatched", agentId: "a2", at: now - 100 },
          { id: "e3", kind: "run_started", agentId: "a3", at: now - 100 },
        ],
      },
      1500
    );
    expect(got).toEqual([{ id: "e3", agentId: "a3" }]);
  });
});
