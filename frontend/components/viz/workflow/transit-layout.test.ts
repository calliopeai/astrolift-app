import { describe, expect, it } from "vitest";

import {
  makeWorkflows,
  type FanoutBranch,
  type FanoutStation,
  type WorkflowTrain,
} from "../core/workflow-model";

import {
  HOLD_SHORT,
  TRANSIT,
  branchSlot,
  layoutTransit,
  lerpPlace,
  loopShortLabel,
  particlePeriod,
  placePoint,
  pointAlong,
  positionOnLine,
  segmentPath,
  stationRounds,
  trainPlace,
  trainT,
  truncate,
  tweenStart,
  visibleBranches,
  workerSpeed,
} from "./transit-layout";

const train = (over: Partial<WorkflowTrain>): WorkflowTrain => ({
  id: "t",
  lineId: "l0",
  label: "#1",
  at: 1,
  progress: 0.5,
  state: "moving",
  startedAt: 0,
  ...over,
});

describe("transit layout", () => {
  it("jogs at 45 degrees between rows of different height", () => {
    const p = segmentPath({ x: 0, y: 0 }, { x: 100, y: 20 });
    expect(p).toHaveLength(4);
    const [, b, c] = p;
    expect(c.x - b.x).toBe(c.y - b.y);
    expect(segmentPath({ x: 0, y: 5 }, { x: 50, y: 5 })).toHaveLength(2);
  });

  it("lays every line out in its own row, fitting the widest line", () => {
    const snap = makeWorkflows({ lines: 8 });
    const layout = layoutTransit(snap.lines);
    expect(layout.lines).toHaveLength(8);
    layout.lines.forEach((l, i) => {
      expect(l.stations).toHaveLength(snap.lines[i].stations.length);
      expect(l.segments).toHaveLength(l.stations.length - 1);
      l.stations.forEach((s) => expect(s.x).toBeLessThanOrEqual(layout.width));
    });
    const ys = layout.lines.map((l) => Math.max(...l.stations.map((s) => s.y)));
    for (let i = 1; i < ys.length; i++) expect(ys[i]).toBeGreaterThan(ys[i - 1]);
  });

  it("walks a polyline by length", () => {
    const p = pointAlong(
      [
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 10, y: 10 },
      ],
      0.75
    );
    expect(p.x).toBeCloseTo(10);
    expect(p.y).toBeCloseTo(5);
    expect(p.angle).toBeCloseTo(90);
  });

  it("places trains between stations, held ones short of the gate", () => {
    expect(trainT(train({}), 6)).toBe(1.5);
    expect(trainT(train({ state: "held", at: 3 }), 6)).toBeCloseTo(3 - HOLD_SHORT);
    expect(trainT(train({ state: "done", at: 2 }), 6)).toBe(5);
    const line = layoutTransit(makeWorkflows({ lines: 1 }).lines).lines[0];
    const mid = positionOnLine(line, 0.5);
    expect(mid.x).toBeGreaterThan(line.stations[0].x);
    expect(mid.x).toBeLessThan(line.stations[1].x);
    expect(positionOnLine(line, 99).x).toBe(line.stations.at(-1)!.x);
  });

  it("truncates names and spaces particles by rate", () => {
    expect(truncate("Transform", 12)).toBe("Transform");
    expect(truncate("A very long station name", 8)).toBe("A very …");
    expect(particlePeriod(0.9)).toBeLessThan(particlePeriod(0.1));
    [0, 0.3, 0.6, 0.9].forEach((r) => expect(24 % particlePeriod(r)).toBe(0));
  });
});

const shaped = makeWorkflows({ lines: 0, trainsPerLine: 0, shapes: true });
const [feature, research, support, close, reconcile] = shaped.lines;

describe("transit layout: shapes", () => {
  it("keeps classic lines exactly as before", () => {
    const classic = makeWorkflows({ lines: 3 }).lines;
    const layout = layoutTransit(classic);
    expect(layout.height).toBe(TRANSIT.padTop + 3 * TRANSIT.rowH + TRANSIT.padBottom);
    layout.lines.forEach((l) => {
      expect(l.loops).toEqual([]);
      expect(l.sidings).toEqual([]);
      expect(l.swarms).toEqual([]);
    });
  });

  it("arcs back-edges above the line, taller over the loops they span", () => {
    const g = layoutTransit([feature]).lines[0];
    const [test, review, retry] = g.loops;
    const y = g.stations[0].y;
    for (const loop of [test, review, retry]) {
      loop.poly.forEach((p) => expect(p.y).toBeLessThan(y));
      expect(loop.label).toMatch(/max \d+ (rounds|attempts)$/);
    }
    const top = (l: typeof test) => Math.min(...l.poly.map((p) => p.y));
    expect(top(review)).toBeLessThan(top(test));
    // A back-edge runs from its source back to its target.
    expect(test.poly[0].x).toBeGreaterThan(test.poly.at(-1)!.x);
    expect(Math.abs(test.poly[0].x - g.stations[2].x)).toBeLessThan(10);
    expect(Math.abs(test.poly.at(-1)!.x - g.stations[1].x)).toBeLessThan(10);
    // A retry is a small loop over its one station.
    expect(Math.abs(retry.poly[0].x - g.stations[5].x)).toBeLessThan(10);
    expect(Math.abs(retry.poly.at(-1)!.x - g.stations[5].x)).toBeLessThan(10);
    // The row makes room for the tallest arc and its label.
    expect(top(review) - 12).toBeGreaterThan(TRANSIT.padTop - 1);
  });

  it("labels a loop with its trigger and bound", () => {
    expect(loopShortLabel(feature, feature.loops![0])).toBe("test failed · max 5 rounds");
    expect(loopShortLabel(feature, feature.loops![1])).toBe("review rejected · max 3 rounds");
    expect(loopShortLabel(feature, feature.loops![2])).toBe("deploy failed · max 3 attempts");
    const long = {
      ...feature,
      stations: feature.stations.map((s) => ({ ...s, name: `${s.name} across every ledger` })),
    };
    expect(loopShortLabel(long, feature.loops![0])).toBe("test across… failed · max 5 rounds");
  });

  it("puts a train riding a loop on its return arc, and glides it on and off", () => {
    const g = layoutTransit([feature]).lines[0];
    const loopId = feature.loops![0].id;
    const t = {
      id: "r",
      lineId: feature.id,
      label: "#1",
      at: 2,
      progress: 0,
      state: "moving" as const,
      startedAt: 0,
    };
    const riding = trainPlace({ ...t, onLoopId: loopId, loopProgress: 0.5 }, feature, g);
    expect(riding).toEqual({ track: "loop", loopId, f: 0.5 });
    const p = placePoint(g, riding);
    expect(p.y).toBeLessThan(g.stations[2].y - 20);
    // Leaving the station onto the loop starts at the arc's foot.
    expect(tweenStart({ track: "line", t: 2.9 }, riding, g)).toEqual({
      track: "loop",
      loopId,
      f: 0,
    });
    // Arriving back starts at the loop's target, not where the arc was.
    expect(tweenStart(riding, { track: "line", t: 1.1 }, g)).toEqual({ track: "line", t: 1 });
    // A re-entry at the first station jumps.
    expect(tweenStart({ track: "line", t: 4 }, { track: "line", t: 0 }, g)).toEqual({
      track: "line",
      t: 0,
    });
    expect(lerpPlace({ track: "loop", loopId, f: 0 }, riding, 0.5)).toEqual({
      track: "loop",
      loopId,
      f: 0.25,
    });
  });

  it("badges rounds past the first, amber one round from the bound", () => {
    const [test, review] = feature.loops!;
    const base = {
      lineId: feature.id,
      label: "#1",
      progress: 0,
      state: "moving" as const,
      startedAt: 0,
    };
    const rounds = stationRounds(feature, [
      { ...base, id: "a", at: 2, loopRounds: { [test.id]: 2 } },
      { ...base, id: "b", at: 3, state: "held", loopRounds: { [review.id]: 1 } },
      { ...base, id: "c", at: 2 },
    ]);
    expect(rounds.get(2)).toMatchObject({ round: 3, near: false, trainId: "a" });
    expect(rounds.get(3)).toMatchObject({ round: 2, near: true });
    expect(stationRounds(feature, [{ ...base, id: "c", at: 2 }]).size).toBe(0);
  });

  it("splits a fanout into sidings that merge at its join, capped at eight", () => {
    const branches = (n: number): FanoutBranch[] =>
      Array.from({ length: n }, (_, i) => ({ id: `b${i}`, label: `B${i}`, state: "running" }));
    const withBranches = (n: number) => ({
      ...research,
      stations: research.stations.map((s) =>
        s.kind === "fanout" ? ({ ...s, branches: branches(n), runId: "r" } as FanoutStation) : s
      ),
    });
    const g = layoutTransit([withBranches(5)]).lines[0];
    const [sd] = g.sidings;
    expect(sd).toMatchObject({ fanout: 1, join: 2, hidden: 0, placeholder: false });
    expect(sd.tracks).toHaveLength(5);
    expect(g.sidingSegments).toEqual([1]);
    const ys = sd.tracks.map((t) => t.slots.running.y);
    expect(new Set(ys).size).toBe(5);
    sd.tracks.forEach((t) => {
      expect(t.slots.queued.x).toBeLessThan(t.slots.running.x);
      expect(t.slots.running.x).toBeLessThan(t.slots.settled.x);
    });
    const wide = layoutTransit([withBranches(11)]).lines[0].sidings[0];
    expect(wide.tracks).toHaveLength(8);
    expect(wide.hidden).toBe(3);
    expect(visibleBranches(branches(11)).hidden).toHaveLength(3);
    // No run has set the count yet: dashed placeholders.
    expect(layoutTransit([research]).lines[0].sidings[0]).toMatchObject({ placeholder: true });
    expect(branchSlot("failed")).toBe("settled");
    expect(branchSlot("queued")).toBe("queued");
  });

  it("holds a run at the fanout split while its branches work", () => {
    const g = layoutTransit([research]).lines[0];
    const t = {
      id: "r",
      lineId: research.id,
      label: "#1",
      at: 1,
      progress: 0.6,
      state: "moving" as const,
      startedAt: 0,
    };
    expect(trainPlace(t, research, g)).toEqual({ track: "line", t: 1 });
    expect(trainPlace({ ...t, state: "held" }, research, g)).toEqual({
      track: "line",
      t: 1 - HOLD_SHORT,
    });
  });

  it("gives a supervisor a swarm above it; busy workers circle by load, idle ones hold still", () => {
    const g = layoutTransit([support]).lines[0];
    const [sw] = g.swarms;
    expect(sw.station).toBe(1);
    expect(sw.workers).toHaveLength(5);
    expect(sw.hive.y).toBeLessThan(g.stations[1].y);
    expect(workerSpeed(false, 0.9)).toBe(0);
    expect(workerSpeed(true, 0.9)).toBeGreaterThan(workerSpeed(true, 0.1));
  });

  it("draws a child line only while its nested station is open, under the parent", () => {
    const lines = [close, reconcile];
    expect(layoutTransit(lines).lines.map((l) => l.id)).toEqual([close.id]);
    const open = layoutTransit(lines, new Set([close.stations[1].id]));
    expect(open.lines.map((l) => l.id)).toEqual([close.id, reconcile.id]);
    const [parent, child] = open.lines;
    expect(child.depth).toBe(1);
    expect(child.parent).toEqual({ lineId: close.id, station: 1 });
    expect(child.stations[0].x).toBe(parent.stations[1].x);
    expect(child.stations[0].y).toBeGreaterThan(parent.stations[1].y);
    expect(open.height).toBeGreaterThan(layoutTransit(lines).height);
  });
});
