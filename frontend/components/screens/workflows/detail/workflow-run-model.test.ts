import { describe, expect, it } from "vitest";

import {
  execution,
  FAILED_EXECUTIONS,
  FAILED_RUN,
  GATE_EXECUTIONS,
  LOOP_EXECUTIONS,
  LOOP_PLAN,
  LOOP_RUN,
  NOW,
  PLAN,
  RUN,
} from "./workflow-run.fixtures";
import {
  configuredRunOutcome,
  configuredRunSubject,
  liveRunsSnapshot,
  placeExecutions,
  runLogLines,
  runReplayTimeline,
  runRounds,
  runSnapshot,
  workflowRunHref,
} from "./workflow-run-model";

describe("placeExecutions", () => {
  it("opens a round when work goes back to an earlier stage, with the cause", () => {
    const placed = placeExecutions(LOOP_PLAN, LOOP_EXECUTIONS);
    expect(placed.map((p) => p.round)).toEqual([1, 1, 2, 2, 2, 2]);
    expect(placed[2]!.causedBy?.reason).toBe("tester failed: 3 specs failed in checkout");
  });

  it("numbers a fan-out's executions as branches within the round", () => {
    const placed = placeExecutions(PLAN, GATE_EXECUTIONS);
    expect(placed.filter((p) => p.execution.stageOrder === 0).map((p) => p.branch)).toEqual([
      1, 2, 3,
    ]);
    expect(placed.find((p) => p.execution.stageOrder === 1)!.branch).toBeNull();
  });

  it("marks a second attempt at the same stage as a retry, not a round", () => {
    const placed = placeExecutions(LOOP_PLAN, [
      execution("x1", 0, "agent_dispatch", { status: "failed", errorMessage: "OOMKilled" }),
      execution("x2", 0, "agent_dispatch", { attemptNumber: 2, startedAt: "2026-09-28T14:03:00Z" }),
    ]);
    expect(placed.map((p) => p.round)).toEqual([1, 1]);
    expect(placed[1]!.causedBy?.reason).toBe("Attempt 2 after: OOMKilled");
  });
});

describe("runRounds", () => {
  it("groups a looped run by round and leaves the round's cause on the round", () => {
    const rounds = runRounds(LOOP_PLAN, LOOP_EXECUTIONS, LOOP_RUN, NOW);
    expect(rounds.map((r) => [r.round, r.items.map((i) => i.name)])).toEqual([
      [1, ["coder", "tester"]],
      [2, ["coder", "tester", "Gate · code review", "deployer"]],
    ]);
    expect(rounds[0]!.state).toBe("failed");
    expect(rounds[1]!.cause).toMatch(/tester failed/);
    expect(rounds[1]!.items[0]!.cause).toBeNull();
  });

  it("folds a fan-out into one step with its branches, and holds the waiting gate", () => {
    const [round] = runRounds(PLAN, GATE_EXECUTIONS, RUN, NOW);
    const fan = round!.items[0]!;
    expect(fan.branches).toHaveLength(3);
    expect(fan.plannedBranches).toBe(3);
    expect(fan.state).toBe("ok");
    const gate = round!.items.find((i) => i.gate);
    expect(gate?.state).toBe("waiting");
    // Outreach has not started: pending while the run is live.
    expect(round!.items.at(-1)).toMatchObject({
      name: "bdr-outreach",
      planned: true,
      state: "pending",
    });
  });

  it("skips the stages a failed run never reached", () => {
    const [round] = runRounds(PLAN, FAILED_EXECUTIONS, FAILED_RUN, NOW);
    expect(round!.items[0]!.state).toBe("failed");
    expect(round!.items.slice(1).every((i) => i.planned && i.state === "skipped")).toBe(true);
  });

  it("links a nested workflow's execution to the child's run page", () => {
    const nested = [
      {
        ...PLAN[0]!,
        kind: "workflow",
        agentName: "",
        workflowRef: "inner-triage",
        fanOutCount: null,
      },
    ];
    const [round] = runRounds(
      nested,
      [
        execution("x-child", 0, "workflow", {
          status: "running",
          endedAt: null,
          childWorkflowDefinitionSlug: "resolved-inner",
          childWorkflowRunGuid: "child-run-guid",
        }),
      ],
      RUN,
      NOW
    );
    expect(round!.items[0]).toMatchObject({
      name: "Workflow · inner-triage",
      childHref: "/workflows/resolved-inner/runs/child-run-guid",
    });
  });
});

describe("runReplayTimeline", () => {
  it("puts a fan-out on the main track and its branches on lanes", () => {
    const timeline = runReplayTimeline(PLAN, GATE_EXECUTIONS, RUN, "run")!;
    const fan = timeline.stages.find((s) => s.kind === "fanout")!;
    expect(fan.finishedAt).toBe(Date.UTC(2026, 8, 28, 14, 5, 0));
    expect(timeline.stages.filter((s) => s.branchId)).toHaveLength(3);
    expect(timeline.stages.find((s) => s.kind === "gate")!.status).toBe("waiting");
    expect(timeline.finishedAt).toBeNull();
  });

  it("carries the loop's cause and the round onto the replay", () => {
    const timeline = runReplayTimeline(LOOP_PLAN, LOOP_EXECUTIONS, LOOP_RUN, "run")!;
    expect(timeline.stages[2]).toMatchObject({
      round: 2,
      causedBy: { reason: expect.stringMatching(/tester failed/) },
    });
    expect(timeline.finishedAt).toBe(Date.UTC(2026, 8, 28, 14, 7, 0));
  });

  it("has nothing to replay before a stage starts", () => {
    expect(runReplayTimeline(PLAN, [], RUN, "run")).toBeNull();
  });
});

describe("runSnapshot", () => {
  it("holds the run at the gate, with the fan-out's branches as they settled", () => {
    const snap = runSnapshot(PLAN, GATE_EXECUTIONS, RUN, {
      lineId: "l",
      name: "Outbound",
      now: NOW,
    });
    const line = snap.lines[0]!;
    expect(line.stations.map((s) => s.kind)).toEqual(["fanout", "join", "gate", "stage"]);
    expect(snap.trains[0]).toMatchObject({ at: 2, state: "held" });
    const fan = line.stations[0]!;
    expect(fan.kind === "fanout" && fan.branches.map((b) => b.state)).toEqual([
      "succeeded",
      "succeeded",
      "succeeded",
    ]);
  });

  it("marks a finished run done at the end of the line", () => {
    const snap = runSnapshot(LOOP_PLAN, LOOP_EXECUTIONS, LOOP_RUN, {
      lineId: "l",
      name: "Feature",
      now: NOW,
    });
    expect(snap.trains[0]).toMatchObject({ at: 3, state: "done", round: 2 });
  });
});

describe("liveRunsSnapshot", () => {
  it("places only live runs whose current stage the source reports", () => {
    const snap = liveRunsSnapshot(
      PLAN,
      [RUN, { ...RUN, guid: "r2", currentStageOrder: null }, LOOP_RUN],
      {
        lineId: "l",
        name: "Outbound",
        now: NOW,
      }
    );
    expect(snap!.trains.map((t) => t.id)).toEqual([RUN.guid]);
  });

  it("is null with nothing to place", () => {
    expect(liveRunsSnapshot(PLAN, [LOOP_RUN], { lineId: "l", name: "x", now: NOW })).toBeNull();
  });
});

describe("runLogLines", () => {
  it("merges stage events and the engine history in time order, errors as errors", () => {
    const lines = runLogLines(LOOP_PLAN, LOOP_EXECUTIONS.slice(0, 2), [
      {
        eventType: "WorkflowExecutionStarted",
        timestamp: "2026-09-28T13:59:59Z",
        payload: {},
        retryCount: 0,
        decision: "",
      },
    ]);
    expect(lines[0]!.message).toBe("engine WorkflowExecutionStarted");
    expect(lines.at(-1)).toMatchObject({
      level: "error",
      message: "tester failed: 3 specs failed in checkout",
    });
  });
});

describe("configuredRunOutcome", () => {
  it("keeps expired history terminal with an unknown outcome", () => {
    const run = {
      guid: "expired",
      currentState: "expired",
      isCompleted: false,
      startedAt: new Date(NOW).toISOString(),
      completedAt: new Date(NOW).toISOString(),
      temporalWorkflowId: "expired",
      temporalRunId: "old",
      triggerKind: "manual",
    };
    expect(configuredRunOutcome(run)).toBe("unknown");
    expect(configuredRunSubject(run).live).toBe(false);
  });
  it("reads free-text states through the terminal check", () => {
    const base = {
      guid: "g",
      temporalWorkflowId: null,
      temporalRunId: null,
      startedAt: "",
      completedAt: null,
    };
    expect(configuredRunOutcome({ ...base, currentState: "stage_2", isCompleted: false })).toBe(
      "running"
    );
    expect(configuredRunOutcome({ ...base, currentState: "stage_2", isCompleted: true })).toBe(
      "succeeded"
    );
    expect(configuredRunOutcome({ ...base, currentState: "Failed", isCompleted: true })).toBe(
      "failed"
    );
  });
});

it("builds the run page href", () => {
  expect(workflowRunHref("a b", "r/1")).toBe("/workflows/a%20b/runs/r%2F1");
});
