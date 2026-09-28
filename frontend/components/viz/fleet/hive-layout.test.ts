import { describe, expect, it } from "vitest";

import { makeFleet } from "../core/fleet-model";

import { hexPath, hexRadius, layoutHive, loadMix, truncate } from "./hive-layout";

describe("layoutHive", () => {
  it("gives every agent one cell and every cluster a hub first", () => {
    const fleet = makeFleet({ clusters: 3, agentsPerCluster: 7 });
    const layout = layoutHive(fleet.clusters, fleet.agents, 768);
    expect(layout.groups).toHaveLength(3);
    expect(layout.byAgent.size).toBe(21);
    for (const g of layout.groups) {
      expect(g.hub.agentId).toBeNull();
      expect(g.cells).toHaveLength(7);
      expect(g.cells.every((c) => c.clusterId === g.clusterId)).toBe(true);
    }
  });

  it("stays inside the width and wraps clusters at 500 agents", () => {
    const fleet = makeFleet({ clusters: 10, agentsPerCluster: 50 });
    const layout = layoutHive(fleet.clusters, fleet.agents, 768);
    const cells = layout.groups.flatMap((g) => [g.hub, ...g.cells]);
    expect(cells).toHaveLength(510);
    // A pointy-top hex is sqrt(3) * r wide and 2r tall.
    const half = (Math.sqrt(3) / 2) * layout.r;
    for (const c of cells) {
      expect(c.x - half).toBeGreaterThanOrEqual(-0.01);
      expect(c.x + half).toBeLessThanOrEqual(768 + 0.01);
      expect(c.y + layout.r).toBeLessThanOrEqual(layout.height + 0.01);
    }
    expect(new Set(layout.groups.map((g) => g.y)).size).toBeGreaterThan(1);
  });

  it("never overlaps two cells", () => {
    const fleet = makeFleet({ clusters: 4, agentsPerCluster: 30 });
    const layout = layoutHive(fleet.clusters, fleet.agents, 960);
    const cells = layout.groups.flatMap((g) => [g.hub, ...g.cells]);
    const minGap = Math.sqrt(3) * layout.r - 0.01;
    for (let i = 0; i < cells.length; i++) {
      for (let j = i + 1; j < cells.length; j++) {
        const d = Math.hypot(cells[i].x - cells[j].x, cells[i].y - cells[j].y);
        expect(d).toBeGreaterThanOrEqual(minGap);
      }
    }
  });

  it("handles an empty cluster and no clusters", () => {
    const fleet = makeFleet({ clusters: 2, agentsPerCluster: 0 });
    expect(layoutHive(fleet.clusters, [], 400).groups[0].cells).toEqual([]);
    expect(layoutHive([], [], 400).groups).toEqual([]);
  });
});

describe("helpers", () => {
  it("shrinks tiles as the fleet grows", () => {
    expect(hexRadius(10)).toBeGreaterThan(hexRadius(300));
  });
  it("draws a closed six-point hex", () => {
    expect(hexPath(10).match(/L/g)).toHaveLength(5);
    expect(hexPath(10).endsWith("Z")).toBe(true);
  });
  it("maps load to a clamped mix", () => {
    expect(loadMix(0)).toBe(8);
    expect(loadMix(1)).toBe(70);
    expect(loadMix(2)).toBe(70);
  });
  it("truncates with an ellipsis", () => {
    expect(truncate("prod-east", 20)).toBe("prod-east");
    expect(truncate("prod-east-long", 6)).toBe("prod-…");
  });
});
