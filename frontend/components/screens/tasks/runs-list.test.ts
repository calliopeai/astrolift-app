import { describe, expect, it } from "vitest";

import {
  effectiveFilters,
  parseListState,
  RESERVED_PARAMS,
} from "@/components/list/use-list-state";
import { taskRun } from "@/components/screens/jobs/jobs-tasks.fixtures";

import { workflowRun } from "./runs.fixtures";
import {
  activeSources,
  AGENT_RUNS_LIST,
  type AgentTaskSource,
  filterRunRows,
  fromAgentTask,
  fromTaskRun,
  fromWorkflowRun,
  pageRunRows,
  RUNS_LIST,
  sortRunRows,
} from "./runs-list";

const NOW = Date.UTC(2026, 8, 28, 12, 0, 0);

function agentTask(id: string, patch: Partial<AgentTaskSource> = {}): AgentTaskSource {
  return {
    id,
    agentSlug: "support-bot",
    agentName: "Support bot",
    projectSlug: "sales",
    status: "completed",
    createdAt: "2026-09-28T11:00:00Z",
    startedAt: "2026-09-28T11:00:05Z",
    finishedAt: "2026-09-28T11:01:05Z",
    vncEnabled: false,
    vncUrl: "",
    ...patch,
  };
}

const ALL = { agent: true, workflow: true, task: true };

describe("the mappers", () => {
  it("maps an agent task, with Cancel and Watch live only while it runs", () => {
    const done = fromAgentTask(agentTask("a1"));
    expect(done).toMatchObject({
      key: "agent:a1",
      kind: "agent",
      subject: "support-bot",
      agent: "support-bot",
      project: "sales",
      outcome: "succeeded",
      durationSeconds: 60,
      href: "/agents/runs/a1",
      cancel: null,
      watchHref: null,
    });
    const live = fromAgentTask(
      agentTask("a2", { status: "running", finishedAt: null, vncEnabled: true, vncUrl: "/vnc/a2" })
    );
    expect(live.cancel).toEqual({ kind: "agent", id: "a2" });
    expect(live.watchHref).toBe("/agents/runs/a2/vnc");
  });

  it("maps a workflow run, cancelling by its Temporal id", () => {
    const r = fromWorkflowRun(
      workflowRun("w1", { status: "running", endedAt: null, parentRunGuid: "w0" })
    );
    expect(r).toMatchObject({
      kind: "workflow",
      workflow: "nightly-sync",
      trigger: "parent run",
      outcome: "running",
      cancel: { kind: "workflow", workflowId: "wf-w1" },
    });
  });

  it("maps a task run, marking the viewer's own as Mine", () => {
    const r = fromTaskRun(taskRun("t1", { triggeredByUsername: "leo" }), "leo");
    expect(r).toMatchObject({ kind: "task", app: "billing", startedByMe: true, cancel: null });
    expect(fromTaskRun(taskRun("t2"), null).startedByMe).toBe(false);
    expect(fromTaskRun(taskRun("t3", { status: "pending" }), null).outcome).toBe("waiting");
  });
});

describe("activeSources", () => {
  it("fetches only what the chips leave room for", () => {
    expect(activeSources({}, ALL)).toEqual(ALL);
    expect(activeSources({ agent: "support-bot" }, ALL)).toEqual({
      agent: true,
      workflow: false,
      task: false,
    });
    expect(activeSources({ project: "sales" }, ALL)).toEqual({
      agent: true,
      workflow: true,
      task: false,
    });
    expect(activeSources({ kind: "workflow" }, ALL)).toEqual({
      agent: false,
      workflow: true,
      task: false,
    });
  });

  it("never fetches a module the viewer cannot see", () => {
    expect(activeSources({}, { agent: true, workflow: false, task: false })).toEqual({
      agent: true,
      workflow: false,
      task: false,
    });
  });
});

describe("the views", () => {
  it("lead with All and Mine, then the spec's four", () => {
    expect(RUNS_LIST.views.map((v) => v.label)).toEqual([
      "All",
      "Mine",
      "Running",
      "Failed",
      "Waiting",
      "Scheduled",
    ]);
    const failed = parseListState(RUNS_LIST, "view=failed&agent=support-bot");
    expect(effectiveFilters(RUNS_LIST, failed)).toEqual({
      status: "failed",
      agent: "support-bot",
    });
    expect(AGENT_RUNS_LIST.fields.map((f) => f.key)).not.toContain("agent");
  });

  it("uses no reserved list param as a field", () => {
    const reserved: readonly string[] = RESERVED_PARAMS;
    for (const f of RUNS_LIST.fields) expect(reserved).not.toContain(f.key);
  });
});

describe("filter, sort, page", () => {
  const rows = [
    fromAgentTask(agentTask("a1")),
    fromAgentTask(
      agentTask("a2", { status: "failed", startedAt: "2026-09-28T11:30:00Z", finishedAt: null })
    ),
    fromWorkflowRun(workflowRun("w1", { startedAt: "2026-09-28T11:45:00Z" })),
    fromTaskRun(
      taskRun("t1", { triggerKind: "workflow", startedAt: "2026-09-27T09:00:00Z" }),
      "leo"
    ),
  ];

  it("filters on each chip and on search", () => {
    const keys = (f: Record<string, string>, q = "") =>
      filterRunRows(rows, f, q, NOW).map((r) => r.key);
    expect(keys({ status: "failed" })).toEqual(["agent:a2"]);
    expect(keys({ kind: "workflow" })).toEqual(["workflow:w1"]);
    expect(keys({ agent: "support" })).toEqual(["agent:a1", "agent:a2"]);
    expect(keys({ trigger: "Workflow" })).toEqual(["task:t1"]);
    expect(keys({ startedBy: "me" })).toEqual(["task:t1"]);
    expect(keys({ since: "24h" })).toEqual(["agent:a1", "agent:a2", "workflow:w1"]);
    expect(keys({}, "NIGHTLY")).toEqual(["workflow:w1"]);
  });

  it("sorts newest first by default, by took on request, blanks last", () => {
    expect(sortRunRows(rows, []).map((r) => r.key)).toEqual([
      "workflow:w1",
      "agent:a2",
      "agent:a1",
      "task:t1",
    ]);
    const byTook = sortRunRows(rows, [{ key: "took", dir: "desc" }]).map((r) => r.key);
    expect(byTook.at(-1)).toBe("agent:a2");
    expect(byTook[0]).toBe("workflow:w1");
  });

  it("pages by offset", () => {
    const first = pageRunRows(rows, null, 3);
    expect(first.rows).toHaveLength(3);
    expect(first.nextCursor).toBe("o:3");
    const last = pageRunRows(rows, first.nextCursor, 3);
    expect(last.rows).toHaveLength(1);
    expect(last.nextCursor).toBeNull();
  });
});
