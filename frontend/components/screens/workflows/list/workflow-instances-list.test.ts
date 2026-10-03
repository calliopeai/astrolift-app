import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import {
  formatInstanceDuration,
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
  it("uses server cursor pages and declares unsupported search truthfully", () => {
    expect(WORKFLOW_INSTANCES_LIST.paging).toBe("cursor");
    expect(WORKFLOW_INSTANCES_LIST.searchable).toBe(false);
    expect(
      instancesVariables({ type: "DeployAppWorkflow", status: "running" }, 50, "opaque")
    ).toEqual({
      workflowType: "DeployAppWorkflow",
      status: "RUNNING",
      limit: 50,
      after: "opaque",
    });
    expect(query().variables).toEqual({ workflowType: null, status: null, limit: 25, after: null });
  });

  it("preserves the entire server page and order despite legacy browser paging parameters", () => {
    const result = query("q=does-not-match&sort=duration&page=9");
    expect(result.rows).toEqual(ALL);
    expect(result.totalCount).toBe(ALL.length);
    expect(query("view=mine").rows).toEqual([]);
  });

  it("formats durations", () => {
    expect(formatInstanceDuration(42.3)).toBe("42.3s");
    expect(formatInstanceDuration(754)).toBe("12m 34s");
    expect(formatInstanceDuration(7260)).toBe("2h 1m");
  });
});
