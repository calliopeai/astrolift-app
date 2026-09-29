import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import {
  formatInstanceDuration,
  INSTANCES_LIMIT,
  instancesVariables,
  selectInstances,
  WORKFLOW_INSTANCES_LIST,
} from "./workflow-instances-list";
import { INSTANCES, LONG_INSTANCE } from "./workflows-list.fixtures";

const ALL = [
  ...INSTANCES,
  { ...LONG_INSTANCE, status: "FAILED", startedAt: "2026-09-27T09:00:00Z" },
];

function query(qs = "") {
  const state = parseListState(WORKFLOW_INSTANCES_LIST, qs);
  const filters = effectiveFilters(WORKFLOW_INSTANCES_LIST, state);
  return {
    variables: instancesVariables(filters),
    ...selectInstances(ALL, {
      filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    }),
  };
}

describe("platform instances list", () => {
  it("leads with All and Mine, and every view says the browser pages the one read", () => {
    expect(WORKFLOW_INSTANCES_LIST.views.map((v) => v.key)).toEqual([
      "all",
      "mine",
      "running",
      "failed",
    ]);
    for (const v of WORKFLOW_INSTANCES_LIST.views) {
      expect(v.note).toContain(`newest ${INSTANCES_LIMIT} matching instances`);
    }
  });

  it("sends Type and Status to the server, upper-casing the status, at the page cap", () => {
    expect(query().variables).toEqual({ workflowType: null, status: null, limit: 200 });
    expect(query("view=running").variables.status).toBe("RUNNING");
    expect(query("type=DeployAppWorkflow&status=timed_out").variables).toEqual({
      workflowType: "DeployAppWorkflow",
      status: "TIMED_OUT",
      limit: 200,
    });
  });

  it("filters what came back by view, chip and search", () => {
    expect(query().totalCount).toBe(ALL.length);
    expect(query("view=failed").rows.map((i) => i.status)).toEqual(["FAILED"]);
    expect(query("type=ProvisionClusterWorkflow").rows.map((i) => i.workflowType)).toEqual([
      "ProvisionClusterWorkflow",
    ]);
    expect(query("q=drift").rows.map((i) => i.workflowId)).toEqual(["drift-detect-nightly-9e8d"]);
    // The actor is a display name, not the viewer's account.
    expect(query("view=mine").totalCount).toBe(0);
  });

  it("sorts newest first by default, and by duration or type on request", () => {
    const started = query().rows.map((i) => Date.parse(i.startedAt));
    expect(started).toEqual([...started].sort((a, b) => b - a));
    const took = query("sort=-duration").rows.map((i) => i.durationSeconds ?? -1);
    expect(took).toEqual([...took].sort((a, b) => b - a));
    const types = query("sort=type").rows.map((i) => i.workflowType.toLowerCase());
    expect(types).toEqual([...types].sort());
  });

  it("pages in the browser", () => {
    const many = Array.from({ length: 30 }, (_, i) => ({
      ...INSTANCES[0],
      workflowId: `w-${i}`,
      runId: `r-${i}`,
    }));
    const page2 = selectInstances(many, {
      filters: {},
      q: "",
      sort: WORKFLOW_INSTANCES_LIST.defaultSort,
      page: 2,
      pageSize: 25,
    });
    expect(page2).toMatchObject({ totalCount: 30 });
    expect(page2.rows).toHaveLength(5);
  });

  it("formats durations", () => {
    expect(formatInstanceDuration(42.3)).toBe("42.3s");
    expect(formatInstanceDuration(754)).toBe("12m 34s");
    expect(formatInstanceDuration(7260)).toBe("2h 1m");
  });
});
