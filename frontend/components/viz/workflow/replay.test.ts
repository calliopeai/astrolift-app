import { describe, expect, it } from "vitest";

import { mulberry32 } from "../core/semantics";
import { makeFeatureTimeline, makeTimeline } from "../core/workflow-model";

import {
  boundaries,
  formatClock,
  formatDuration,
  laneState,
  makeFanoutTimeline,
  makeLiveRun,
  placeRun,
  roundAt,
  runEnd,
  slowestStage,
  stageIndexAt,
  stepBoundary,
  stepLiveRun,
} from "./replay";

describe("placeRun", () => {
  it("scales stages to their real durations", () => {
    const run = placeRun(makeTimeline());
    expect(run.total).toBe(913_000);
    expect(run.stages[0]).toMatchObject({ start: 0, end: 94_000 });
    expect(run.stages[3].end - run.stages[3].start).toBe(380_000);
  });

  it("runs an open stage to now", () => {
    const live = makeLiveRun(0);
    expect(runEnd(live.timeline, 5_000)).toBe(5_000);
    expect(placeRun(live.timeline, 5_000).stages[0]).toMatchObject({ start: 0, end: 5_000 });
  });
});

describe("stageIndexAt", () => {
  it("finds the stage under the playhead and ignores skipped stages", () => {
    const run = placeRun(makeTimeline({ failedAt: 1 }));
    expect(stageIndexAt(run, -1)).toBe(-1);
    expect(stageIndexAt(run, 0)).toBe(0);
    expect(stageIndexAt(run, 100_000)).toBe(1);
    expect(stageIndexAt(run, 800_000)).toBe(1);
  });
});

describe("stepBoundary", () => {
  it("steps between stage boundaries and stops at the ends", () => {
    const run = placeRun(makeTimeline());
    expect(boundaries(run)).toEqual([0, 94_000, 306_000, 347_000, 727_000, 847_000, 913_000]);
    expect(stepBoundary(run, 0, 1)).toBe(94_000);
    expect(stepBoundary(run, 94_000, 1)).toBe(306_000);
    expect(stepBoundary(run, 100_000, -1)).toBe(94_000);
    expect(stepBoundary(run, 0, -1)).toBe(0);
    expect(stepBoundary(run, 913_000, 1)).toBe(913_000);
  });
});

describe("slowestStage", () => {
  it("names the longest stage that ran and its share", () => {
    const slow = slowestStage(placeRun(makeTimeline()))!;
    expect(slow.stage.name).toBe("Approve");
    expect(slow.ms).toBe(380_000);
    expect(Math.round(slow.share * 100)).toBe(42);
  });
});

describe("format", () => {
  it("reads durations and clocks", () => {
    expect(formatDuration(380_000)).toBe("6m 20s");
    expect(formatDuration(41_000)).toBe("41s");
    expect(formatDuration(3_720_000)).toBe("1h 2m");
    expect(formatClock(252_000)).toBe("04:12");
    expect(formatClock(3_729_000)).toBe("1:02:09");
  });
});

describe("stepLiveRun", () => {
  it("opens stages in order, holds at the gate, and finishes", () => {
    let run = makeLiveRun(0);
    const rng = mulberry32(3);
    let sawWaiting = false;
    for (let i = 0; i < 2_000 && run.timeline.finishedAt === null; i++) {
      run = stepLiveRun(run, rng, 1_000);
      if (run.timeline.stages.at(-1)!.status === "waiting") sawWaiting = true;
    }
    expect(sawWaiting).toBe(true);
    expect(run.timeline.finishedAt).not.toBeNull();
    expect(run.timeline.stages.map((s) => s.name)).toEqual([
      "Build",
      "Test",
      "Scan",
      "Approve",
      "Canary",
      "Rollout",
    ]);
    expect(run.timeline.stages[3].decidedBy).toBe("ops@example.com");
    expect(run.timeline.stages.every((s) => s.status === "succeeded")).toBe(true);
  });
});

describe("rounds", () => {
  it("places each round and marks where a loop sent work back", () => {
    const run = placeRun(makeFeatureTimeline());
    expect(run.rounds.map((r) => r.round)).toEqual([1, 2, 3, 4]);
    expect(run.rounds[0]).toMatchObject({ start: 0, causeIndex: null });
    run.rounds.slice(1).forEach((r, i) => {
      expect(r.start).toBe(run.rounds[i].end);
      expect(r.causeIndex).not.toBeNull();
    });
    expect(run.marks.map((m) => [m.round, m.to, m.reason])).toEqual([
      [2, "Code", "Test failed: 3 specs in checkout"],
      [3, "Code", "Test failed: 1 spec in checkout"],
      [4, "Code", "Review rejected: needs a migration"],
    ]);
    expect(run.marks.every((m, i) => m.at === run.rounds[i + 1].start)).toBe(true);
    expect(roundAt(run, run.marks[1].at)!.round).toBe(3);
    expect(roundAt(run, -1)).toBeNull();
  });

  it("keeps a run that never looped to one round and no marks", () => {
    const run = placeRun(makeTimeline());
    expect(run.rounds).toHaveLength(1);
    expect(run.marks).toHaveLength(0);
    expect(run.lanes).toBe(0);
  });
});

describe("fan-out branches", () => {
  const run = placeRun(makeFanoutTimeline());
  const branches = run.stages.filter((p) => p.lane > 0);

  it("gives each branch a lane of its own, side by side in time", () => {
    expect(run.lanes).toBe(6);
    expect(branches.map((p) => p.lane)).toEqual([1, 2, 3, 4, 5, 6]);
    const fan = run.stages.find((p) => p.stage.kind === "fanout")!;
    branches.forEach((p) => {
      expect(p.start).toBeGreaterThanOrEqual(fan.start);
      expect(p.end).toBeLessThanOrEqual(fan.end);
    });
    expect(Math.max(...branches.map((p) => p.end))).toBe(fan.end);
  });

  it("keeps the playhead and the slowest stage on the main track", () => {
    const fan = run.stages.findIndex((p) => p.stage.kind === "fanout");
    expect(stageIndexAt(run, run.stages[fan].start + 1_000)).toBe(fan);
    expect(slowestStage(run)!.stage.name).toBe("Fan out");
  });

  it("lights and settles each branch on its own times", () => {
    const [first, , , slow] = branches;
    const t = first.end + 1;
    expect(laneState(first, t)).toBe("settled");
    expect(laneState(slow, t)).toBe("active");
    expect(laneState(slow, slow.start - 1)).toBe("ahead");
    expect(boundaries(run)).toContain(first.end);
  });

  it("runs an open branch to the live edge and keeps it lit there", () => {
    const tl = makeFanoutTimeline({ now: 0 });
    const open = {
      ...tl,
      finishedAt: null,
      stages: tl.stages.map((s) =>
        s.branchId === "b3" ? { ...s, finishedAt: null, status: "running" as const } : s
      ),
    };
    const placed = placeRun(open, tl.finishedAt!);
    const b = placed.stages.find((p) => p.stage.branchId === "b3")!;
    expect(b.end).toBe(placed.total);
    expect(laneState(b, placed.total)).toBe("active");
  });
});
