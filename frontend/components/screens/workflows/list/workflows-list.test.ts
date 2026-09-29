import { describe, expect, it } from "vitest";

import { joinWorkflows, selectWorkflows, WORKFLOWS_LIST } from "./workflows-list";
import {
  CONFIGURED,
  DEFINITIONS,
  HISTORICAL_RUNS,
  REPO_WORKFLOW,
  RUNNING_RUNS,
  WORKFLOW_ROWS,
} from "./workflows-list.fixtures";

const select = (filters: Record<string, string> = {}, q = "", page = 1, pageSize = 25) =>
  selectWorkflows(WORKFLOW_ROWS, {
    q,
    filters,
    sort: WORKFLOWS_LIST.defaultSort,
    page,
    pageSize,
  });

describe("joinWorkflows", () => {
  it("makes a row per configured workflow and per definition, templates apart", () => {
    const kinds = WORKFLOW_ROWS.map((r) => [r.slug, r.kind]);
    expect(kinds).toContainEqual(["weekly-dependency-audit", "configured"]);
    expect(kinds).toContainEqual(["emr-triage", "definition"]);
    expect(kinds).toContainEqual(["code-review-loop", "template"]);
    expect(WORKFLOW_ROWS).toHaveLength(CONFIGURED.length + DEFINITIONS.length);
  });

  it("gives a configured workflow its definition's stages, shape and project", () => {
    const row = WORKFLOW_ROWS.find((r) => r.slug === "weekly-dependency-audit")!;
    expect(row.stageCount).toBe(3);
    expect(row.line.stations.map((s) => s.kind)).toEqual(["stage", "fanout", "join"]);
    expect(row.lastRun).toBe("failed");
  });

  it("takes a definition's last run from the definition runs", () => {
    const row = WORKFLOW_ROWS.find((r) => r.slug === "emr-triage")!;
    expect(row.lastRun).toBe("running");
  });

  it("says unknown, not never, when the runs page may not reach a definition's last run", () => {
    const [row] = joinWorkflows([], [REPO_WORKFLOW], [], { runsComplete: false });
    expect(row!.lastRun).toBe("unknown");
    const [complete] = joinWorkflows([], [REPO_WORKFLOW], [], { runsComplete: true });
    expect(complete!.lastRun).toBe("never");
  });

  it("says unknown for a configured workflow that ran when the list has no runs for it", () => {
    const [row] = joinWorkflows([{ ...CONFIGURED[0]!, runs: [], runCount: 4 }], [], [], {
      runsComplete: true,
    });
    expect(row!.lastRun).toBe("unknown");
    expect(row!.stageCount).toBeNull();
  });

  it("offers Delete only for org-owned definitions not synced from a repository", () => {
    const deletable = (slug: string) => WORKFLOW_ROWS.find((r) => r.slug === slug)!.deletable;
    expect(deletable("emr-triage")).toBe(false);
    expect(deletable("nightly-drift-report")).toBe(true);
    expect(deletable("code-review-loop")).toBe(false);
    expect(deletable("weekly-dependency-audit")).toBe(true);
  });

  it("ignores run ordering", () => {
    const rows = joinWorkflows([], [REPO_WORKFLOW], [...HISTORICAL_RUNS, ...RUNNING_RUNS], {
      runsComplete: true,
    });
    expect(rows[0]!.lastRun).toBe("running");
  });
});

describe("selectWorkflows", () => {
  it("keeps templates out of All and only templates in Templates", () => {
    expect(select().rows.some((r) => r.kind === "template")).toBe(false);
    expect(select({ template: "1" }).rows.every((r) => r.kind === "template")).toBe(true);
  });

  it("filters by pattern, project and last run", () => {
    expect(select({ pattern: "supervisor_worker" }).rows.map((r) => r.slug)).toEqual([
      "incident-responder",
    ]);
    expect(select({ project: "EMR-BUG-TRIAGE" }).rows.map((r) => r.slug)).toEqual(["emr-triage"]);
    expect(select({ status: "failed" }).rows.map((r) => r.slug)).toEqual([
      "weekly-dependency-audit",
    ]);
  });

  it("Mine is empty until workflows record a creator", () => {
    expect(select({ mine: "1" }).totalCount).toBe(0);
  });

  it("searches name, slug, definition and source", () => {
    expect(select({}, "smd-agents").rows.map((r) => r.slug)).toEqual(["emr-triage"]);
    expect(select({}, "research fan").rows.map((r) => r.slug)).toEqual(["weekly-dependency-audit"]);
  });

  it("sorts by name and pages", () => {
    const { rows, totalCount } = select({}, "", 2, 2);
    const all = select().rows.map((r) => r.name);
    expect([...all].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))).toEqual(all);
    expect(totalCount).toBe(all.length);
    expect(rows.map((r) => r.name)).toEqual(all.slice(2, 4));
  });
});
