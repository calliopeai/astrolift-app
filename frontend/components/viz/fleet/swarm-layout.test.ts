import { describe, expect, it } from "vitest";

import { makeFleet } from "../core/fleet-model";

import {
  PULSE_MS,
  placeHives,
  quadPoint,
  seedSwarmer,
  swarmRadius,
  swarmSpeed,
  tendrilWidth,
  travellingPulses,
} from "./swarm-layout";

describe("swarm layout", () => {
  const fleet = makeFleet({ clusters: 3, agentsPerCluster: 4 });
  const hives = placeHives(fleet);

  it("places one hive per cluster, inside the view", () => {
    expect(hives).toHaveLength(3);
    for (const h of hives) {
      expect(h.x).toBeGreaterThan(0);
      expect(h.y).toBeGreaterThan(0);
    }
  });

  it("keeps idle agents still and busy ones moving faster", () => {
    const a = fleet.agents[0];
    expect(swarmSpeed({ ...a, health: "idle" })).toBe(0);
    expect(swarmSpeed({ ...a, health: "ok", load: 0.9 })).toBeGreaterThan(
      swarmSpeed({ ...a, health: "ok", load: 0.1 })
    );
  });

  it("drifts failing agents out beyond the healthy swarm", () => {
    const a = fleet.agents[0];
    const seed = seedSwarmer(a, 0, 4);
    const hive = hives[0];
    expect(swarmRadius({ ...a, health: "failing" }, hive, seed)).toBeGreaterThan(
      swarmRadius({ ...a, health: "ok" }, hive, seed)
    );
  });

  it("seeds each agent the same way every time", () => {
    const a = fleet.agents[1];
    expect(seedSwarmer(a, 1, 4)).toEqual(seedSwarmer(a, 1, 4));
  });

  it("thickens tendrils with active runs, capped", () => {
    expect(tendrilWidth(3)).toBeGreaterThan(tendrilWidth(0));
    expect(tendrilWidth(50)).toBe(4);
  });

  it("walks the quadratic curve end to end", () => {
    const a = { x: 0, y: 0 };
    const b = { x: 10, y: 0 };
    expect(quadPoint(a, { x: 5, y: 10 }, b, 0)).toEqual(a);
    expect(quadPoint(a, { x: 5, y: 10 }, b, 1)).toEqual(b);
  });

  it("only reports dispatches still in flight", () => {
    const s = {
      ...fleet,
      events: [
        { id: "e1", kind: "dispatched" as const, agentId: "a0", at: fleet.now - PULSE_MS / 2 },
        { id: "e2", kind: "dispatched" as const, agentId: "a1", at: fleet.now - PULSE_MS * 2 },
        { id: "e3", kind: "run_started" as const, agentId: "a2", at: fleet.now },
      ],
    };
    expect(travellingPulses(s, fleet.now).map((p) => p.id)).toEqual(["e1"]);
  });
});
