import { describe, expect, it } from "vitest";

import { makeWorkflows, type WorkflowTrain } from "../core/workflow-model";

import {
  COL_W,
  NODE_H,
  describeWorkflowGraph,
  gateState,
  layoutWorkflowGraph,
  pointAlong,
  runRound,
  stationHealth,
  truncate,
} from "./workflow-graph-layout";

const train = (at: number, state: WorkflowTrain["state"]): WorkflowTrain => ({
  id: `t${at}${state}`,
  lineId: "l0",
  label: "#1",
  at,
  progress: 0,
  state,
  startedAt: 0,
});

describe("gateState", () => {
  it("reads denied, waiting, approved, or nothing from the runs", () => {
    expect(gateState([train(3, "failed"), train(3, "held")], 3)).toBe("denied");
    expect(gateState([train(3, "held")], 3)).toBe("waiting");
    expect(gateState([train(4, "moving")], 3)).toBe("approved");
    expect(gateState([train(1, "moving")], 3)).toBeNull();
  });
});

describe("stationHealth", () => {
  it("ranks failure over backlog over work over idle", () => {
    expect(stationHealth([train(2, "failed"), train(2, "moving")], 2, 9)).toBe("failing");
    expect(stationHealth([train(2, "moving")], 2, 4)).toBe("degraded");
    expect(stationHealth([train(2, "moving")], 2, 3)).toBe("ok");
    expect(stationHealth([train(1, "moving")], 2, 0)).toBe("idle");
  });
});

describe("truncate", () => {
  it("keeps short names and ellipsizes long ones to the limit", () => {
    expect(truncate("Build", 13)).toBe("Build");
    const t = truncate("a-very-long-stage-name", 10);
    expect(t).toHaveLength(10);
    expect(t.endsWith("…")).toBe(true);
  });
});

describe("layoutWorkflowGraph", () => {
  it("lays each line out as its own lane with stations a column apart", () => {
    const s = makeWorkflows({ lines: 3 });
    const layout = layoutWorkflowGraph(s);
    expect(layout.lanes).toHaveLength(3);
    const release = layout.nodes.filter((n) => n.lineId === "l0");
    expect(release).toHaveLength(6);
    expect(release[1].x - release[0].x).toBe(COL_W);
    expect(new Set(layout.lanes.map((l) => l.y)).size).toBe(3);
    expect(layout.edges).toHaveLength(s.segments.length);
  });

  it("marks backed-up segments degraded and groups runs by station and state", () => {
    const s = makeWorkflows({ backedUp: true });
    const layout = layoutWorkflowGraph(s);
    expect(layout.edges.some((e) => e.backedUp && e.health === "degraded")).toBe(true);
    const total = layout.groups.reduce((n, g) => n + g.trains.length, 0);
    expect(total).toBe(s.trains.length);
  });

  it("treats zero throughput as idle", () => {
    const s = makeWorkflows({ trainsPerLine: 0 });
    const idle = { ...s, segments: s.segments.map((g) => ({ ...g, rate: 0, backlog: 0 })) };
    const layout = layoutWorkflowGraph(idle);
    expect(layout.edges.every((e) => !e.flowing && e.health === "idle")).toBe(true);
    expect(layout.nodes.every((n) => n.health === "idle")).toBe(true);
  });
});

describe("describeWorkflowGraph", () => {
  it("summarises lines and run states", () => {
    const s = makeWorkflows({ lines: 2, trainsPerLine: 0 });
    expect(describeWorkflowGraph(s)).toBe("2 workflows, 0 running, 0 held at gates, 0 failed");
  });
});

describe("pointAlong", () => {
  it("walks a polyline by length", () => {
    const pts = [
      { x: 0, y: 0 },
      { x: 0, y: 10 },
      { x: 30, y: 10 },
    ];
    expect(pointAlong(pts, 0)).toEqual({ x: 0, y: 0 });
    expect(pointAlong(pts, 0.25)).toEqual({ x: 0, y: 10 });
    expect(pointAlong(pts, 0.5)).toEqual({ x: 10, y: 10 });
    expect(pointAlong(pts, 1)).toEqual({ x: 30, y: 10 });
  });
});

describe("the shapes", () => {
  const s = makeWorkflows({ lines: 0, shapes: true });
  const [feature] = s.lines;
  const layout = layoutWorkflowGraph(s);

  it("says a run's round against the loop it is inside", () => {
    const onRound2 = s.trains.find((t) => t.lineId === feature.id && t.round === 2)!;
    expect(runRound(feature, onRound2)).toMatchObject({ text: "round 2 of 5", short: "r2" });
    const at = (i: number) => ({ ...onRound2, at: i, round: 1, loopRounds: {} });
    expect(runRound(feature, at(0))).toBeNull();
    expect(runRound(feature, { ...at(5), attempt: 2 })).toMatchObject({
      text: "attempt 2 of 3",
      near: true,
    });
  });

  it("names every station with its state in words", () => {
    const labels = layout.nodes.map((n) => n.label);
    expect(labels).toContain("Test, round 2 of 5, running");
    expect(labels.find((l) => l.startsWith("Review"))).toBe(
      "Review, gate, round 1 of 3, rejected, sending work back"
    );
    expect(labels.find((l) => l.startsWith("Fan out"))).toMatch(
      /6 branches decided per run, 3 of 6 done, 1 failed/
    );
    expect(labels.find((l) => l.startsWith("Supervisor"))).toMatch(/2 of 5 workers busy/);
    expect(labels.find((l) => l.startsWith("Reconcile"))).toMatch(/child run at Investigate/);
  });

  it("routes back-edges below the lane, each on its own depth, never across the forward row", () => {
    const lane = layout.lanes[0];
    const back = layout.tracks.filter(
      (t) => t.lineId === feature.id && t.loop.kind === "back-edge"
    );
    expect(back.map((t) => t.id)).toEqual([
      `${feature.id}:test-failed`,
      `${feature.id}:review-rejected`,
    ]);
    const depths = back.map((t) => t.points[1].y);
    expect(depths[1]).toBeGreaterThan(depths[0]);
    for (const t of back) {
      // Every point sits at or below the bottom of the stations it joins.
      t.points.forEach((p) => expect(p.y).toBeGreaterThan(lane.y));
      expect(t.points[1].y).toBeGreaterThan(lane.y + NODE_H / 2);
      expect(t.points[1].y).toBeLessThan(lane.top + lane.height);
      expect(t.short).toMatch(/max \d rounds/);
    }
    // The longer loop's legs sit outside the shorter one's, so they never cross.
    expect(back[1].points[3].x).toBeLessThan(back[0].points[3].x);
    expect(back[1].points[0].x).toBeGreaterThan(back[0].points[0].x);
  });

  it("draws a retry as a siding under its stage, with its attempt bound", () => {
    const siding = layout.tracks.find((t) => t.id === `${feature.id}:deploy-retry`)!;
    const deploy = layout.nodes.find((n) => n.station.id === feature.stations[5].id)!;
    expect(siding.short).toBe("max 3 attempts");
    siding.points.forEach((p) => {
      expect(Math.abs(p.x - deploy.x)).toBeLessThan(deploy.w / 2);
      expect(p.y).toBeGreaterThanOrEqual(deploy.y + deploy.h / 2);
    });
  });

  it("puts a run being sent back on its track, and only that track flows", () => {
    expect(layout.loopRuns).toHaveLength(1);
    expect(layout.loopRuns[0].label).toMatch(/sent back from Review to Code for round 2 of 3/);
    expect(layout.tracks.filter((t) => t.active).map((t) => t.id)).toEqual([
      `${feature.id}:review-rejected`,
    ]);
    const grouped = layout.groups.flatMap((g) => g.trains.map((t) => t.id));
    expect(grouped).not.toContain(layout.loopRuns[0].id);
    const badge = layout.groups.find((g) => g.round);
    expect(badge?.round).toEqual({ short: "r2", near: false });
  });

  it("boxes a fanout's branches and hangs a supervisor's workers under it", () => {
    const [fan] = layout.fanouts;
    expect(fan.branches).toHaveLength(6);
    expect(fan.join).toMatchObject({ total: 6, settled: 3, failed: 1 });
    expect(fan.branches.every((b, i) => i === 0 || b.y > fan.branches[i - 1].y)).toBe(true);
    const [swarm] = layout.swarms;
    expect(swarm.workers).toHaveLength(5);
    expect(swarm.workers.filter((w) => w.speed > 0)).toHaveLength(2);
    expect(swarm.cy).toBeGreaterThan(swarm.anchor.y);
  });

  it("draws a nested child line inside its parent's group, collapsible", () => {
    const child = s.lines.find((l) => l.parent)!;
    expect(layout.lanes.map((l) => l.lineId)).not.toContain(child.id);
    const [group] = layout.nested;
    expect(group.expanded).toBe(true);
    const inner = layout.nodes.filter((n) => n.lineId === child.id);
    expect(inner).toHaveLength(3);
    inner.forEach((n) => {
      expect(n.child).toBe(true);
      expect(n.y).toBeGreaterThan(group.y);
      expect(n.y).toBeLessThan(group.y + group.h);
    });
    const shut = layoutWorkflowGraph(s, { collapsed: new Set([group.stationId]) });
    expect(shut.nested[0].expanded).toBe(false);
    expect(shut.nodes.some((n) => n.lineId === child.id)).toBe(false);
    expect(shut.lanes.at(-1)!.height).toBeLessThan(layout.lanes.at(-1)!.height);
  });

  it("keeps classic lanes exactly as tall as before", () => {
    const plain = layoutWorkflowGraph(makeWorkflows());
    expect(plain.lanes.map((l) => l.height)).toEqual([92, 92, 92]);
    expect(plain.tracks).toHaveLength(0);
  });

  it("counts runs sent back in the summary", () => {
    expect(describeWorkflowGraph(s)).toMatch(/1 sent back along a loop$/);
  });
});
