import { describe, expect, it } from "vitest";

import type {
  FanoutStation,
  SupervisorWorker,
  WorkflowSnapshot,
  WorkflowTrain,
} from "../core/workflow-model";
import { makeWorkflows } from "../core/workflow-model";

import {
  BACKLOG_WARN,
  BELT_H,
  BRANCH_S,
  FAN_EXTRA,
  LANE,
  QUEUE_GAP,
  RAIL_SPREAD,
  STEP,
  WIDE_GAP,
  crateLevel,
  easeOut,
  isoViewBox,
  laneLines,
  laneOffsets,
  levelZ,
  loopPoint,
  placeBranches,
  placeCrates,
  railPoint,
  roundBadge,
  runName,
  stationHealth,
  stationName,
  stationX,
  summarize,
  towerDecks,
  towerSpan,
  truncate,
  workerSpeed,
} from "./workflow-iso-layout";

function snap(partial: Partial<WorkflowSnapshot>): WorkflowSnapshot {
  return {
    now: 0,
    lines: [
      {
        id: "l0",
        name: "Release",
        stations: [
          { id: "a", name: "Build", kind: "stage" },
          { id: "b", name: "Approve", kind: "gate" },
          { id: "c", name: "Ship", kind: "stage" },
        ],
      },
    ],
    trains: [],
    segments: [],
    ...partial,
  };
}

const train = (
  id: string,
  at: number,
  state: "moving" | "held" | "failed" | "done",
  progress = 0
) => ({
  id,
  lineId: "l0",
  label: id,
  at,
  progress,
  state,
  startedAt: 0,
});

describe("stationHealth", () => {
  it("is idle with nothing there, ok with a run leaving", () => {
    const s = snap({});
    expect(stationHealth(s, s.lines[0], 0)).toBe("idle");
    const busy = snap({ trains: [train("t", 0, "moving", 0.3)] });
    expect(stationHealth(busy, busy.lines[0], 0)).toBe("ok");
  });

  it("a gate is degraded only while it holds a run", () => {
    const held = snap({ trains: [train("t", 1, "held")] });
    expect(stationHealth(held, held.lines[0], 1)).toBe("degraded");
    const moving = snap({ trains: [train("t", 1, "moving", 0.5)] });
    expect(stationHealth(moving, moving.lines[0], 1)).toBe("idle");
  });

  it("a failed run beats everything; a backed-up intake is degraded", () => {
    const failed = snap({ trains: [train("t", 2, "failed")] });
    expect(stationHealth(failed, failed.lines[0], 2)).toBe("failing");
    const backed = snap({
      segments: [{ lineId: "l0", from: 1, rate: 0.2, backlog: BACKLOG_WARN }],
    });
    expect(stationHealth(backed, backed.lines[0], 2)).toBe("degraded");
  });
});

describe("placeCrates", () => {
  it("moving crates advance with progress between stations", () => {
    const s = snap({ trains: [train("a", 0, "moving", 0), train("b", 0, "moving", 1)] });
    const p = placeCrates(s);
    expect(p.get("a")!.x).toBeLessThan(p.get("b")!.x);
    expect(p.get("b")!.x).toBeLessThan(STEP);
  });

  it("held crates wait before the booth and queue back up the belt", () => {
    const s = snap({ trains: [train("a", 1, "held"), train("b", 1, "held")] });
    const p = placeCrates(s);
    expect(p.get("a")!.x).toBeLessThan(STEP);
    expect(p.get("a")!.x - p.get("b")!.x).toBeCloseTo(QUEUE_GAP);
  });

  it("places every train of a generated snapshot", () => {
    const s = makeWorkflows({ lines: 8, trainsPerLine: 4 });
    expect(placeCrates(s).size).toBe(s.trains.length);
  });
});

describe("helpers", () => {
  it("viewBox is positive and grows with lines", () => {
    const a = isoViewBox(makeWorkflows({ lines: 1 }), 20);
    const b = isoViewBox(makeWorkflows({ lines: 8 }), 20);
    expect(a.w).toBeGreaterThan(0);
    expect(b.h).toBeGreaterThan(a.h);
  });

  it("summarize counts states", () => {
    const s = snap({ trains: [train("a", 1, "held"), train("b", 2, "failed")] });
    expect(summarize(s)).toBe("1 workflow, 2 runs, 0 moving, 1 held at gates, 1 failed");
  });

  it("truncate and easeOut", () => {
    expect(truncate("Transform", 20)).toBe("Transform");
    expect(truncate("Transformation pipeline", 8)).toBe("Transfo…");
    expect(easeOut(0)).toBe(0);
    expect(easeOut(1)).toBe(1);
    expect(easeOut(2)).toBe(1);
  });
});

/* ---------- Shapes ---------- */

// Shaped lines alone: l0 Feature delivery, l1 Research digest, l2 Support triage,
// l3 Quarterly close, l4 Reconcile (the child of l3's nested station).
const base = makeWorkflows({ lines: 0, trainsPerLine: 0, shapes: true });
const [feature, research, support, close] = base.lines;
const [testLoop, reviewLoop, deployRetry] = feature.loops!;

const run = (over: Partial<WorkflowTrain> & Pick<WorkflowTrain, "id" | "lineId" | "at">) => ({
  label: `#${over.id}`,
  progress: 0,
  state: "moving" as const,
  startedAt: 0,
  ...over,
});

const shaped = (trains: WorkflowTrain[], lines = base.lines): WorkflowSnapshot => ({
  ...base,
  lines,
  trains,
});

describe("rounds climb", () => {
  it("builds the tower over the back-edges' span and not over a retry", () => {
    expect(towerSpan(feature)).toEqual({ lo: 1, hi: 3 });
    expect(towerSpan(research)).toBeNull();
  });

  it("a run rides its round's deck over the tower and the belt elsewhere", () => {
    const r3 = run({ id: "a", lineId: feature.id, at: 2, round: 3 });
    expect(crateLevel(feature, r3)).toBe(2);
    expect(crateLevel(feature, { ...r3, at: 4 })).toBe(0);
    expect(crateLevel(feature, { ...r3, state: "done" })).toBe(0);
    const p = placeCrates(shaped([r3, run({ id: "b", lineId: feature.id, at: 2, round: 1 })]));
    expect(p.get("a")!.z).toBeCloseTo(levelZ(2));
    expect(p.get("b")!.z).toBeCloseTo(BELT_H);
  });

  it("lights the deck a run is on and ghosts the earlier rounds", () => {
    const s = shaped([run({ id: "a", lineId: feature.id, at: 2, round: 4 })]);
    expect(towerDecks(s, feature)).toEqual([
      { level: 1, lit: false },
      { level: 2, lit: false },
      { level: 3, lit: true },
    ]);
  });

  it("a run sent back climbs the return ramp one level to the earlier stage", () => {
    const start = loopPoint(feature, testLoop, 1, 0, 0);
    const end = loopPoint(feature, testLoop, 1, 1, 0);
    expect(start.x).toBeCloseTo(stationX(feature, 2));
    expect(end.x).toBeCloseTo(stationX(feature, 1));
    expect(start.z).toBeCloseTo(levelZ(1));
    expect(end.z).toBeCloseTo(levelZ(2));
    const t = run({
      id: "a",
      lineId: feature.id,
      at: 2,
      round: 2,
      onLoopId: testLoop.id,
      loopProgress: 0.5,
    });
    const pose = placeCrates(shaped([t])).get("a")!;
    expect(pose.track!.point(0.5)).toEqual({ x: pose.x, y: pose.y, z: pose.z });
    expect(pose.z).toBeGreaterThan(levelZ(1));
    // The deck it is climbing to is lit, so the tower is one level taller.
    expect(towerDecks(shaped([t]), feature).at(-1)).toEqual({ level: 2, lit: true });
  });

  it("a retry arches over its own stage and lands on the same level", () => {
    const a = loopPoint(feature, deployRetry, 0, 0, 0);
    const mid = loopPoint(feature, deployRetry, 0, 0.5, 0);
    const b = loopPoint(feature, deployRetry, 0, 1, 0);
    expect(a.x).toBeGreaterThan(b.x);
    expect(a.z).toBeCloseTo(b.z);
    expect(mid.z).toBeGreaterThan(1);
  });

  it("badges and names a run by its round against the loop's bound", () => {
    const t = run({
      id: "5310",
      lineId: feature.id,
      at: 2,
      state: "failed",
      round: 3,
      loopRounds: { [testLoop.id]: 2 },
    });
    expect(roundBadge(feature, t)).toBe("3/5");
    expect(runName(shaped([t]), feature, t)).toBe("Run #5310: Test, round 3 of 5, failed");
    const first = run({ id: "1", lineId: feature.id, at: 2 });
    expect(roundBadge(feature, first)).toBeNull();
    const retry = run({ id: "2", lineId: feature.id, at: 5, attempt: 2 });
    expect(roundBadge(feature, retry)).toBe("2/3");
    expect(runName(shaped([retry]), feature, retry)).toContain("attempt 2 of 3");
  });

  it("a station's name carries every loop leaving it, with its bound", () => {
    const s = shaped([]);
    expect(stationName(s, feature, 2)).toBe(
      "Feature delivery, Test: idle. Back to Code when Test fails, at most 5 rounds"
    );
    expect(stationName(s, feature, 3)).toContain("at most 3 rounds");
    expect(reviewLoop.maxRounds).toBe(3);
  });
});

describe("fanout rails", () => {
  it("rails leave the fanout, swing apart, and meet again at the join", () => {
    const y = 0;
    const a0 = railPoint(research, 1, 0, 4, 0, y);
    const a1 = railPoint(research, 1, 0, 4, 1, y);
    const mid = [0, 3].map((b) => railPoint(research, 1, b, 4, 0.5, y).y);
    expect(a0.y).toBeCloseTo(y);
    expect(a1.y).toBeCloseTo(y);
    expect(mid[0]).toBeCloseTo(-RAIL_SPREAD);
    expect(mid[1]).toBeCloseTo(RAIL_SPREAD);
    expect(a1.x).toBeGreaterThan(a0.x);
  });

  it("leaves extra room after a fanout", () => {
    expect(stationX(research, 1)).toBe(STEP);
    expect(stationX(research, 2)).toBeCloseTo(2 * STEP + FAN_EXTRA);
  });

  it("a settled branch's pellet sits at the join, a running one on the way", () => {
    const fan = research.stations[1] as FanoutStation;
    const held: FanoutStation = {
      ...fan,
      runId: "r",
      branches: [
        { id: "b0", label: "Source 1", state: "succeeded" },
        { id: "b1", label: "Source 2", state: "running" },
      ],
    };
    const line = { ...research, stations: research.stations.map((st, j) => (j === 1 ? held : st)) };
    const s = shaped(
      [],
      base.lines.map((l) => (l.id === line.id ? line : l))
    );
    const p = placeBranches(s);
    expect(p.get("b0")!.track!.s).toBe(BRANCH_S.succeeded);
    expect(p.get("b0")!.x).toBeGreaterThan(p.get("b1")!.x);
    expect(p.get("b0")!.live).toBe(true);
    expect(stationName(s, line, 1)).toContain("2 branches, 1 settled, 1 running");
    expect(stationHealth(s, line, 2)).toBe("ok");
  });
});

describe("supervisor and nested workflow", () => {
  it("busy workers orbit faster with more load; idle ones stand still", () => {
    const w = (busy: boolean, load: number): SupervisorWorker => ({
      id: "w",
      label: "Worker 1",
      busy,
      load,
    });
    expect(workerSpeed(w(false, 0.9))).toBe(0);
    expect(workerSpeed(w(true, 0.8))).toBeGreaterThan(workerSpeed(w(true, 0.2)));
    expect(stationName(shaped([]), support, 1)).toBe(
      "Support triage, Supervisor supervisor: 0 of 5 workers busy"
    );
  });

  it("draws the child line on its parent's platform, not as a lane", () => {
    expect(laneLines(base).map((l) => l.name)).toEqual([
      "Feature delivery",
      "Research digest",
      "Support triage",
      "Quarterly close",
    ]);
    const child = run({ id: "p/child", lineId: "l4", at: 1, parentRunId: "p" });
    const parent = run({ id: "p", lineId: close.id, at: 1, childRunId: "p/child" });
    const s = shaped([parent, child]);
    expect(placeCrates(s).has("p/child")).toBe(false);
    const open = placeCrates(s, new Set([close.stations[1].id]));
    expect(open.get("p/child")!.child).toBe(true);
    expect(open.get("p/child")!.lane).toBe(close.id);
    expect(stationName(s, close, 1)).toContain("child run at Investigate, stage 2 of 3");
    expect(runName(s, close, parent)).toBe("Run #p: Reconcile, child run at Investigate");
  });

  it("gives shaped lanes extra room and keeps classic lanes LANE apart", () => {
    expect(laneOffsets(makeWorkflows().lines)).toEqual([0, LANE, 2 * LANE]);
    expect(laneOffsets(laneLines(base))[1]).toBeCloseTo(LANE + WIDE_GAP);
  });

  it("the frame grows with the tower and the open platform", () => {
    const low = isoViewBox(shaped([run({ id: "a", lineId: feature.id, at: 2 })]), 20);
    const high = isoViewBox(shaped([run({ id: "a", lineId: feature.id, at: 2, round: 5 })]), 20);
    expect(high.h).toBeGreaterThan(low.h);
    const openBox = isoViewBox(base, 20, new Set([close.stations[1].id]));
    expect(openBox.h).toBeGreaterThanOrEqual(isoViewBox(base, 20).h);
  });

  it("summarize counts runs sent back on a loop", () => {
    const t = run({
      id: "a",
      lineId: feature.id,
      at: 3,
      onLoopId: reviewLoop.id,
      loopProgress: 0.2,
    });
    expect(summarize(shaped([t]))).toBe(
      "4 workflows, 1 run, 0 moving, 0 held at gates, 0 failed, 1 sent back on a loop"
    );
  });

  it("places every run of a simulated shaped snapshot", () => {
    const s = makeWorkflows({ shapes: true });
    const open = new Set(
      s.lines.flatMap((l) => l.stations.filter((st) => st.kind === "workflow").map((st) => st.id))
    );
    expect(placeCrates(s, open).size).toBe(s.trains.length);
  });
});
