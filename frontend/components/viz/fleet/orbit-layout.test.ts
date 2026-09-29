import { describe, expect, it } from "vitest";

import { makeFleet, stepFleet } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";

import {
  CELL_W,
  DECAY_R,
  LAUNCH_MS,
  MAX_OMEGA,
  RING_R,
  angularSpeed,
  layoutOrbit,
  recentLaunches,
  ringCount,
  ringSlots,
  summarizeFleet,
} from "./orbit-layout";

describe("ringSlots", () => {
  it("uses one to three rings and deals every agent", () => {
    expect(ringCount(4)).toBe(1);
    expect(ringCount(20)).toBe(2);
    expect(ringCount(60)).toBe(3);
    for (const n of [0, 1, 7, 19, 30, 61]) {
      const slots = ringSlots(n, ringCount(n));
      expect(slots.reduce((s, c) => s + c, 0)).toBe(n);
    }
  });

  it("gives outer rings more slots", () => {
    const [a, b, c] = ringSlots(60, 3);
    expect(a).toBeLessThan(b);
    expect(b).toBeLessThan(c);
  });
});

describe("angularSpeed", () => {
  it("is proportional to load and zero when idle", () => {
    expect(angularSpeed("ok", 0.5)).toBeCloseTo(MAX_OMEGA / 2);
    expect(angularSpeed("ok", 1)).toBeCloseTo(MAX_OMEGA);
    expect(angularSpeed("idle", 0.9)).toBe(0);
  });
});

describe("layoutOrbit", () => {
  it("places every agent, failing ones on the decay orbit", () => {
    const fleet = makeFleet({ clusters: 3, agentsPerCluster: 10, failing: 0.3, seed: 5 });
    const layout = layoutOrbit(fleet);
    expect(layout.satellites).toHaveLength(30);
    for (const s of layout.satellites) {
      if (s.health === "failing") expect(s.r).toBe(DECAY_R);
      else expect(RING_R).toContain(s.r);
      if (s.health === "idle") expect(s.omega).toBe(0);
    }
    expect(layout.planets.some((p) => p.hasDecay)).toBe(true);
  });

  it("wraps into the requested columns", () => {
    const fleet = makeFleet({ clusters: 5 });
    const two = layoutOrbit(fleet, 2);
    expect(two.cols).toBe(2);
    expect(two.width).toBe(2 * CELL_W);
    expect(layoutOrbit(fleet, 9).cols).toBe(5);
  });

  it("keeps a healthy agent on its ring when a neighbour fails", () => {
    const fleet = makeFleet({ clusters: 1, agentsPerCluster: 20 });
    const before = layoutOrbit(fleet).satellites;
    const failed = {
      ...fleet,
      agents: fleet.agents.map((a, i) => (i === 0 ? { ...a, health: "failing" as const } : a)),
    };
    const after = layoutOrbit(failed).satellites;
    for (let i = 1; i < before.length; i++) {
      expect(after[i].r).toBe(before[i].r);
      expect(after[i].phase).toBe(before[i].phase);
    }
  });
});

describe("recentLaunches", () => {
  it("draws one launch per recent run_started, above the planet", () => {
    let fleet = makeFleet({ clusters: 2, agentsPerCluster: 8 });
    const rng = mulberry32(2);
    for (let i = 0; i < 4; i++) fleet = stepFleet(fleet, rng, 1200);
    const layout = layoutOrbit(fleet);
    const launches = recentLaunches(fleet, layout, LAUNCH_MS);
    const started = fleet.events.filter(
      (e) => e.kind === "run_started" && fleet.now - e.at < LAUNCH_MS
    );
    expect(launches.map((l) => l.id)).toEqual(started.map((e) => e.id));
    for (const l of launches) {
      const planet = layout.satellites.find((s) => s.id === l.agentId)!;
      expect(l.y).toBeLessThan(planet.cy);
    }
  });

  it("keeps one spark per agent when asked", () => {
    const fleet = makeFleet({ clusters: 1, agentsPerCluster: 2 });
    const withEvents = {
      ...fleet,
      events: [
        { id: "e0", kind: "run_started" as const, agentId: "a0", at: fleet.now - 3000 },
        { id: "e1", kind: "run_started" as const, agentId: "a0", at: fleet.now - 1000 },
      ],
    };
    const layout = layoutOrbit(withEvents);
    expect(recentLaunches(withEvents, layout, 10_000, true).map((l) => l.id)).toEqual(["e1"]);
    expect(recentLaunches(withEvents, layout, 10_000)).toHaveLength(2);
  });
});

describe("summarizeFleet", () => {
  it("counts agents, failing and busy", () => {
    const fleet = makeFleet({ clusters: 2, agentsPerCluster: 3, idle: true });
    expect(summarizeFleet(fleet)).toBe(
      "6 agents across 2 clusters, 0 failing, 0 degraded, 0 busy, 6 idle"
    );
  });
});
