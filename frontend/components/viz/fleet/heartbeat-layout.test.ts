import { describe, expect, it } from "vitest";

import { EVENT_WINDOW_MS, makeFleet, stepFleet } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";

import {
  isBusy,
  layoutHeartbeat,
  placeEvent,
  rowGlowPercent,
  summarizeHeartbeat,
} from "./heartbeat-layout";

describe("placeEvent", () => {
  const ev = { id: "e1", kind: "run_started" as const, agentId: "a0", at: 1000 };

  it("puts a fresh event at now (right edge) at full opacity", () => {
    expect(placeEvent(ev, 1000)).toMatchObject({ x: 100, opacity: 1, age: 0 });
  });

  it("moves an event left and dims it as it ages", () => {
    const dot = placeEvent(ev, 1000 + EVENT_WINDOW_MS / 2)!;
    expect(dot.x).toBeCloseTo(50);
    expect(dot.opacity).toBeCloseTo(0.5);
  });

  it("drops events a window old or older", () => {
    expect(placeEvent(ev, 1000 + EVENT_WINDOW_MS)).toBeNull();
  });

  it("pins future events to now", () => {
    expect(placeEvent(ev, 500)).toMatchObject({ x: 100, age: 0 });
  });
});

describe("layoutHeartbeat", () => {
  it("groups every agent under its cluster, in order, with its own events", () => {
    let fleet = makeFleet({ clusters: 3, agentsPerCluster: 4 });
    const rng = mulberry32(3);
    for (let i = 0; i < 5; i++) fleet = stepFleet(fleet, rng, 1200);
    const groups = layoutHeartbeat(fleet);
    expect(groups.map((g) => g.cluster.id)).toEqual(["c0", "c1", "c2"]);
    expect(groups.flatMap((g) => g.rows).length).toBe(12);
    for (const g of groups)
      for (const r of g.rows) {
        expect(r.agent.clusterId).toBe(g.cluster.id);
        for (const d of r.dots) expect(d.event.agentId).toBe(r.agent.id);
      }
  });
});

describe("busy and glow", () => {
  it("keeps idle agents dark and busier agents brighter", () => {
    const base = {
      id: "a",
      name: "a",
      clusterId: "c0",
      activeRuns: 1,
      queued: 0,
    };
    const idle = { ...base, health: "idle" as const, load: 0, activeRuns: 0 };
    const light = { ...base, health: "ok" as const, load: 0.2 };
    const heavy = { ...base, health: "ok" as const, load: 0.9 };
    expect(isBusy(idle)).toBe(false);
    expect(rowGlowPercent(idle)).toBe(0);
    expect(rowGlowPercent(heavy)).toBeGreaterThan(rowGlowPercent(light));
  });

  it("summarizes the fleet for screen readers", () => {
    const quiet = makeFleet({ idle: true, clusters: 2, agentsPerCluster: 3 });
    expect(summarizeHeartbeat(quiet)).toBe(
      "Heartbeat: 6 agents, 0 failing, 0 busy, 0 events in the last 12s"
    );
  });
});
