import { describe, expect, it } from "vitest";

import { makeWorkflows, type WorkflowTrain } from "../core/workflow-model";

import {
  HOLD_SHORT,
  layoutTransit,
  particlePeriod,
  pointAlong,
  positionOnLine,
  segmentPath,
  trainT,
  truncate,
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
