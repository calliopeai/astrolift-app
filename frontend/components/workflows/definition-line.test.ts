import { describe, expect, it } from "vitest";

import {
  definitionLine,
  definitionSnapshot,
  type LineStage,
  lineShape,
  STAGE_MAX_ATTEMPTS,
} from "./definition-line";

const stage = (order: number, kind: string, patch: Partial<LineStage> = {}): LineStage => ({
  guid: `s${order}`,
  order,
  kind,
  role: "",
  workflowRef: "",
  fanOutCount: null,
  onFailure: "fail",
  ...patch,
});

const DEF = { slug: "outbound", name: "Outbound", patternKind: "fan_out" };

describe("definitionLine", () => {
  it("draws stages in order, whatever order they arrive in", () => {
    const line = definitionLine(DEF, [
      stage(1, "human_gate", { role: "approve" }),
      stage(0, "agent_dispatch", { role: "draft" }),
    ]);
    expect(line.stations.map((s) => [s.name, s.kind])).toEqual([
      ["Draft", "stage"],
      ["Approve", "gate"],
    ]);
    expect(line.id).toBe("definition:outbound");
  });

  it("makes a fanned stage a fanout and the aggregation after it the join that waits on it", () => {
    const line = definitionLine(DEF, [
      stage(0, "agent_dispatch", { role: "research", fanOutCount: 3 }),
      stage(1, "aggregation"),
    ]);
    const [fanout, join] = line.stations;
    expect(fanout).toMatchObject({ kind: "fanout" });
    expect(fanout.kind === "fanout" && fanout.branches.map((b) => b.state)).toEqual([
      "queued",
      "queued",
      "queued",
    ]);
    expect(join).toMatchObject({ kind: "join", waitsOn: 0, name: "Merge" });
  });

  it("leaves a dynamic fan-out's branches to the run", () => {
    const [fanout] = definitionLine(DEF, [
      stage(0, "agent_dispatch", { fanOutDynamic: true }),
    ]).stations;
    expect(fanout).toMatchObject({ kind: "fanout", dynamic: true, branches: [] });
  });

  it("keeps an aggregation with no fan-out before it a plain stage", () => {
    const [merge] = definitionLine(DEF, [stage(0, "aggregation")]).stations;
    expect(merge.kind).toBe("stage");
  });

  it("does not invent supervisor execution semantics for a legacy label", () => {
    const [sup] = definitionLine({ ...DEF, patternKind: "supervisor_worker" }, [
      stage(0, "agent_dispatch", { role: "route", fanOutCount: 2 }),
    ]).stations;
    expect(sup.kind).toBe("fanout");
  });

  it("draws a nested workflow stage by its child's slug", () => {
    const [nested] = definitionLine(DEF, [
      stage(0, "workflow", { workflowRef: "emr-triage" }),
    ]).stations;
    expect(nested).toMatchObject({
      kind: "workflow",
      name: "emr-triage",
      childLineId: "definition:emr-triage",
    });
  });

  it("gives every retrying stage a retry loop bounded by the backend's attempts", () => {
    const line = definitionLine(DEF, [
      stage(0, "agent_dispatch"),
      stage(1, "agent_dispatch", { onFailure: "retry" }),
    ]);
    expect(line.loops).toEqual([
      {
        id: "s1-retry",
        from: 1,
        to: 1,
        trigger: "failed",
        maxRounds: STAGE_MAX_ATTEMPTS,
        kind: "retry",
      },
    ]);
  });

  it("names a stage by role, then agent, then kind", () => {
    const line = definitionLine(DEF, [
      stage(0, "agent_dispatch", { role: "code_review" }),
      stage(1, "agent_dispatch", { agentName: "bdr-outreach" }),
      stage(2, "checkpoint"),
    ]);
    expect(line.stations.map((s) => s.name)).toEqual(["Code review", "bdr-outreach", "Checkpoint"]);
  });
});

describe("definitionSnapshot", () => {
  it("has no runs, so nothing moves", () => {
    const line = definitionLine(DEF, [stage(0, "agent_dispatch")]);
    expect(definitionSnapshot(line)).toEqual({ now: 0, lines: [line], trains: [], segments: [] });
  });
});

describe("lineShape", () => {
  it("says the shape in words", () => {
    const line = definitionLine(DEF, [
      stage(0, "agent_dispatch", { fanOutCount: 3 }),
      stage(1, "aggregation"),
      stage(2, "human_gate", { onFailure: "retry" }),
      stage(3, "workflow", { workflowRef: "child" }),
    ]);
    expect(lineShape(line)).toBe("4 stages: fan-out, join, gate, nested workflow");
    expect(lineShape(definitionLine(DEF, []))).toBe("No stages");
    expect(lineShape(definitionLine(DEF, [stage(0, "agent_dispatch")]))).toBe("1 stage");
  });
});

it("draws the authored earlier-stage return and independent attempt ceiling", () => {
  const line = definitionLine(DEF, [
    stage(0, "agent_dispatch", { outputKey: "draft", onFailure: "retry", maxAttempts: 4 }),
    stage(1, "human_gate", {
      outputKey: "review",
      backEdge: { to: "draft", when: "gate_rejected", max_rounds: 5, on_exhausted: "fail" },
    }),
  ]);
  expect(line.loops).toEqual([
    { id: "s0-retry", from: 0, to: 0, trigger: "failed", maxRounds: 4, kind: "retry" },
    { id: "review->draft", from: 1, to: 0, trigger: "rejected", maxRounds: 5, kind: "back-edge" },
  ]);
});
