import { describe, expect, it } from "vitest";

import { mulberry32 } from "../core/semantics";
import { makeTimeline } from "../core/workflow-model";

import {
  boundaries,
  formatClock,
  formatDuration,
  makeLiveRun,
  placeRun,
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
