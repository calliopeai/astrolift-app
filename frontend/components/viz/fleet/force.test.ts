import { describe, expect, it } from "vitest";

import {
  agentNodeId,
  clusterNodeId,
  createForceSim,
  fleetForceGraph,
  HUB_ID,
  isAtRest,
  pin,
  reheat,
  release,
  runToRest,
  tick,
  type FleetShape,
} from "./force";

function shape(clusters: number, per: number): FleetShape {
  return {
    clusters: Array.from({ length: clusters }, (_, i) => ({
      id: `c${i}`,
      agentIds: Array.from({ length: per }, (_, j) => `a${i}-${j}`),
    })),
    orphans: [],
  };
}

function settled(s: FleetShape, seed = 7) {
  const g = fleetForceGraph(s);
  const sim = createForceSim(g.nodes, g.links, { width: g.width, height: g.height, seed });
  runToRest(sim);
  return sim;
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y);

describe("force simulation", () => {
  it("is deterministic for a seed", () => {
    const a = settled(shape(3, 5));
    const b = settled(shape(3, 5));
    expect(a.nodes.map((n) => [n.x, n.y])).toEqual(b.nodes.map((n) => [n.x, n.y]));
    const c = settled(shape(3, 5), 99);
    expect(c.nodes.map((n) => [n.x, n.y])).not.toEqual(a.nodes.map((n) => [n.x, n.y]));
  });

  it("cools to rest and then stops", () => {
    const g = fleetForceGraph(shape(3, 5));
    const sim = createForceSim(g.nodes, g.links, { width: g.width, height: g.height });
    const ticks = runToRest(sim);
    expect(ticks).toBeGreaterThan(50);
    expect(ticks).toBeLessThan(1000);
    expect(isAtRest(sim)).toBe(true);
    expect(tick(sim)).toBe(false);
  });

  it("keeps every node finite and inside the world", () => {
    const sim = settled(shape(8, 26));
    for (const n of sim.nodes) {
      expect(Number.isFinite(n.x) && Number.isFinite(n.y)).toBe(true);
      expect(n.x).toBeGreaterThanOrEqual(n.radius);
      expect(n.x).toBeLessThanOrEqual(sim.width - n.radius);
      expect(n.y).toBeGreaterThanOrEqual(n.radius);
      expect(n.y).toBeLessThanOrEqual(sim.height - n.radius);
    }
  });

  it("groups agents around their own cluster", () => {
    const sim = settled(shape(4, 6));
    const at = (id: string) => sim.nodes[sim.index.get(id)!];
    for (let c = 0; c < 4; c++) {
      const agent = at(agentNodeId(`a${c}-0`));
      const own = dist(agent, at(clusterNodeId(`c${c}`)));
      for (let o = 0; o < 4; o++) {
        if (o === c) continue;
        expect(own).toBeLessThan(dist(agent, at(clusterNodeId(`c${o}`))));
      }
    }
  });

  it("does not stack agents on top of each other", () => {
    const sim = settled(shape(3, 12));
    const agents = sim.nodes.filter((n) => n.id.startsWith("a:"));
    let closest = Infinity;
    for (let i = 0; i < agents.length; i++)
      for (let j = i + 1; j < agents.length; j++)
        closest = Math.min(closest, dist(agents[i], agents[j]));
    expect(closest).toBeGreaterThan(agents[0].radius);
  });

  it("holds a pinned node while the rest settle, and frees it on release", () => {
    const g = fleetForceGraph(shape(2, 4));
    const sim = createForceSim(g.nodes, g.links, { width: g.width, height: g.height });
    pin(sim, HUB_ID, 100, 120);
    runToRest(sim);
    const hub = sim.nodes[sim.index.get(HUB_ID)!];
    expect([hub.x, hub.y]).toEqual([100, 120]);
    release(sim, HUB_ID);
    reheat(sim, 0.5);
    runToRest(sim);
    expect(hub.x).not.toBe(100);
  });

  it("carries positions and pins into a rebuilt sim, and settles gently", () => {
    const g1 = fleetForceGraph(shape(2, 4));
    const first = createForceSim(g1.nodes, g1.links, { width: g1.width, height: g1.height });
    runToRest(first);
    pin(first, clusterNodeId("c0"), 50, 60);
    const g2 = fleetForceGraph(shape(2, 5));
    const next = createForceSim(g2.nodes, g2.links, {
      width: g2.width,
      height: g2.height,
      carry: first,
    });
    const hubBefore = first.nodes[first.index.get(HUB_ID)!];
    const hubAfter = next.nodes[next.index.get(HUB_ID)!];
    expect([hubAfter.x, hubAfter.y]).toEqual([hubBefore.x, hubBefore.y]);
    expect(next.nodes[next.index.get(clusterNodeId("c0"))!].fx).toBe(50);
    expect(next.alpha).toBeLessThan(1);
    // The new agent is seeded near its cluster, not at the centre.
    const fresh = next.nodes[next.index.get(agentNodeId("a1-4"))!];
    const parent = next.nodes[next.index.get(clusterNodeId("c1"))!];
    expect(dist(fresh, parent)).toBeLessThan(80);
  });
});

describe("fleetForceGraph", () => {
  it("links hub to clusters to agents, and orphans to the hub", () => {
    const g = fleetForceGraph({ clusters: [{ id: "x", agentIds: ["1", "2"] }], orphans: ["3"] });
    expect(g.nodes.map((n) => n.id)).toEqual([HUB_ID, "c:x", "a:1", "a:2", "a:3"]);
    expect(g.links.map((l) => `${l.source}>${l.target}`)).toEqual([
      "hub>c:x",
      "c:x>a:1",
      "c:x>a:2",
      "hub>a:3",
    ]);
  });

  it("grows the world with the fleet", () => {
    expect(fleetForceGraph(shape(8, 26)).width).toBeGreaterThan(fleetForceGraph(shape(3, 5)).width);
  });
});
