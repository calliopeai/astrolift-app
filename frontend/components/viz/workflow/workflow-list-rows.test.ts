import { describe, expect, it } from "vitest";

import { makeWorkflows, type WorkflowTrain } from "../core/workflow-model";

import { loopWords, workflowRows, type LoopRow, type StageRow } from "./workflow-list-rows";

const s = makeWorkflows({ lines: 0, shapes: true });
const [feature] = s.lines;
const rows = workflowRows(s);
const stage = (name: string) =>
  rows.find((r): r is StageRow => r.type === "stage" && r.station.name === name)!;
const loops = rows.filter((r): r is LoopRow => r.type === "loop");

describe("workflowRows", () => {
  it("has one row per stage and one per loop, each loop right after the stage it leaves", () => {
    const stages = s.lines.reduce((n, l) => n + l.stations.length, 0);
    expect(rows.filter((r) => r.type === "stage")).toHaveLength(stages);
    expect(loops).toHaveLength(3);
    const i = rows.findIndex((r) => r.id === `${feature.id}:test-failed`);
    expect(rows[i - 1]).toMatchObject({ type: "stage", index: 2 });
  });

  it("states the round, attempts and branch progress", () => {
    expect(stage("Test").round).toEqual({ text: "2 / 5", near: false });
    expect(stage("Deploy").attempts).toBe("1 / 3");
    expect(stage("Fan out").progress).toBe("3 / 6 done, 1 failed");
    expect(stage("Fan out").state).toBe("Running");
    expect(stage("Summarise").progress).toBe("Waits on 6 branches, 3 to go");
    expect(stage("Supervisor").progress).toBe("2 / 5 sub-tasks, 2 of 5 workers busy");
    expect(stage("Reconcile").progress).toBe("Child run at Investigate, 2 / 3");
    expect(stage("Match").workflow).toBe("Quarterly close / Reconcile");
  });

  it("says each loop in words, with its bound", () => {
    const [test, review, retry] = loops;
    expect(test.words).toBe(
      "Back to Code when Test fails, at most 5 rounds; #1200 is on round 2 of 5"
    );
    expect(review).toMatchObject({
      words: "Review rejected goes back to Code, round 2 of 3",
      state: "Sending back",
      round: { text: "2 / 3", near: true },
    });
    expect(retry).toMatchObject({ kind: "Retry", state: "Idle" });
    expect(retry.words).toBe("Retry Deploy when it fails, at most 3 attempts");
  });
});

describe("loopWords", () => {
  const [testLoop, , retry] = feature.loops!;
  const run = (over: Partial<WorkflowTrain>): WorkflowTrain => ({
    id: "r",
    lineId: feature.id,
    label: "#9",
    at: 2,
    progress: 0,
    state: "moving",
    startedAt: 0,
    ...over,
  });

  it("names the round a run is being sent back into", () => {
    const t = run({ onLoopId: testLoop.id, loopRounds: { [testLoop.id]: 2 } });
    expect(loopWords(feature, testLoop, [t])).toBe("Test failed goes back to Code, round 4 of 5");
  });

  it("names the attempt a retry is going for", () => {
    const t = run({ at: 5, onLoopId: retry.id, attempt: 1 });
    expect(loopWords(feature, retry, [t])).toBe("Deploy failed, retrying Deploy, attempt 2 of 3");
  });
});
