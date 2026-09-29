import { describe, expect, it } from "vitest";

import { makeFeatureTimeline, makeWorkflows, type WorkflowTrain } from "./workflow-model";
import {
  groupTimelineByRound,
  isNearBound,
  joinReady,
  loopLabel,
  loopRound,
  loopsAt,
  roundLabel,
  roundsLeft,
} from "./workflow-shapes";

const feature = makeWorkflows({ lines: 0, trainsPerLine: 0, shapes: true }).lines[0];
const [testLoop, reviewLoop, deployRetry] = feature.loops!;

const train = (over: Partial<WorkflowTrain> = {}): WorkflowTrain => ({
  id: "t",
  lineId: feature.id,
  label: "#1",
  at: 2,
  progress: 0,
  state: "moving",
  startedAt: 0,
  ...over,
});

describe("workflow shapes", () => {
  it("finds the loops leaving a station, a retry included", () => {
    expect(loopsAt(feature, 2)).toEqual([testLoop]);
    expect(loopsAt(feature, 3)).toEqual([reviewLoop]);
    expect(loopsAt(feature, 5)).toEqual([deployRetry]);
    expect(loopsAt(feature, 1)).toEqual([]);
    expect(loopsAt({ ...feature, loops: undefined }, 2)).toEqual([]);
  });

  it("counts rounds per back-edge and attempts per retry", () => {
    expect(loopRound(train(), testLoop)).toBe(1);
    const t = train({ loopRounds: { [testLoop.id]: 2 }, attempt: 2 });
    expect(loopRound(t, testLoop)).toBe(3);
    expect(roundsLeft(t, testLoop)).toBe(2);
    expect(loopRound(t, reviewLoop)).toBe(1);
    expect(loopRound(t, deployRetry)).toBe(2);
    expect(roundsLeft(train({ loopRounds: { [testLoop.id]: 9 } }), testLoop)).toBe(0);
    expect(roundLabel(t, testLoop)).toBe("round 3 of 5");
    expect(roundLabel(t, deployRetry)).toBe("attempt 2 of 3");
  });

  it("warns one round from the bound", () => {
    expect(isNearBound(train({ loopRounds: { [testLoop.id]: 2 } }), testLoop)).toBe(false);
    expect(isNearBound(train({ loopRounds: { [testLoop.id]: 3 } }), testLoop)).toBe(true);
    expect(isNearBound(train({ attempt: 1 }), deployRetry)).toBe(false);
    expect(isNearBound(train({ attempt: 2 }), deployRetry)).toBe(true);
  });

  it("says every loop with its bound", () => {
    expect(loopLabel(feature, testLoop)).toBe("Back to Code when Test fails, at most 5 rounds");
    expect(loopLabel(feature, reviewLoop)).toBe(
      "Back to Code when Review is rejected, at most 3 rounds"
    );
    expect(loopLabel(feature, deployRetry)).toBe("Retry Deploy when it fails, at most 3 attempts");
    const cond = { ...testLoop, trigger: "condition" as const, condition: "coverage drops" };
    expect(loopLabel(feature, cond)).toBe("Back to Code when coverage drops, at most 5 rounds");
  });

  it("groups a timeline by round", () => {
    const groups = groupTimelineByRound(makeFeatureTimeline());
    expect(groups.map((g) => g.round)).toEqual([1, 2, 3, 4]);
    expect(groups[0].stages.map((s) => s.name)).toEqual(["Plan", "Code", "Test"]);
    expect(groups[3].stages.map((s) => s.name)).toEqual([
      "Code",
      "Test",
      "Review",
      "Merge",
      "Deploy",
    ]);
    const bare = groupTimelineByRound({
      runId: "r",
      label: "r",
      startedAt: 0,
      finishedAt: 2,
      stages: [
        { id: "b", name: "B", kind: "stage", startedAt: 1, finishedAt: 2, status: "succeeded" },
        { id: "a", name: "A", kind: "stage", startedAt: 0, finishedAt: 1, status: "succeeded" },
      ],
    });
    expect(bare).toHaveLength(1);
    expect(bare[0].stages.map((s) => s.id)).toEqual(["a", "b"]);
  });

  it("lets a join go only when every branch has settled", () => {
    expect(joinReady([]).ready).toBe(false);
    const b = (state: "queued" | "running" | "succeeded" | "failed", i = 0) => ({
      id: `b${i}`,
      label: `B${i}`,
      state,
    });
    const waiting = joinReady([b("succeeded"), b("running", 1), b("queued", 2)]);
    expect(waiting).toMatchObject({ ready: false, total: 3, settled: 1, pending: 2 });
    const done = joinReady([b("succeeded"), b("failed", 1)]);
    expect(done).toMatchObject({ ready: true, succeeded: 1, failed: 1, pending: 0 });
  });
});
