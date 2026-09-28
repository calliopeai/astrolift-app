import { describe, expect, it } from "vitest";

import { makeWorkflows, type WorkflowTrain } from "../core/workflow-model";

import {
  COL_W,
  describeWorkflowGraph,
  gateState,
  layoutWorkflowGraph,
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
