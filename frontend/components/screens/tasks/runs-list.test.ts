import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState, RESERVED_PARAMS } from "@/components/list/list-state";
import { taskRun } from "@/components/screens/jobs/jobs-tasks.fixtures";

import { auditRun, UPCOMING_RUNS, workflowRun } from "./runs.fixtures";
import {
  AGENT_RUNS_LIST,
  type AgentTaskSource,
  agentRunCrumbs,
  agentTasksVariables,
  fromAgentTask,
  fromRunAuditItem,
  fromTaskRun,
  fromUpcomingRun,
  fromWorkflowRun,
  RUN_KINDS,
  RUNS_LIST,
  runAuditVariables,
  statusOutcome,
  upcomingNextCursor,
  upcomingVariables,
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
    triggerKind: "manual",
    triggeredByMe: true,
    ...patch,
  };
}

/** A URL query as the cursor list state the hook reads. */
function stateOf(def: typeof RUNS_LIST, qs: string) {
  const state = parseListState(def, qs);
  return {
    filters: effectiveFilters(def, state),
    q: state.q,
    sort: state.sort,
    after: state.after,
    pageSize: state.pageSize,
  };
}

describe("the mappers", () => {
  it("maps a run audit row to its own page, with Cancel only while live and Retry once finished", () => {
    const done = fromRunAuditItem(auditRun("a1"));
    expect(done).toMatchObject({
      key: "agent:a1",
      kind: "agent",
      subject: "bdr-outreach",
      outcome: "succeeded",
      trigger: "manual",
      startedBy: "Leo Mata",
      startedByMe: true,
      durationSeconds: 60,
      href: "/agents/runs/a1",
      cancel: null,
      retry: { id: "a1" },
      watchHref: null,
    });
    const running = fromRunAuditItem(
      auditRun("a2", { status: "running", outcome: "running", endedAt: null }),
      { vncEnabled: true, vncUrl: "/vnc/a2" }
    );
    expect(running.cancel).toEqual({ kind: "agent", id: "a2" });
    expect(running.retry).toBeNull();
    expect(running.watchHref).toBe("/agents/runs/a2/vnc");
    // A running run the page read no live session for has no Watch live.
    expect(
      fromRunAuditItem(auditRun("a3", { status: "running", outcome: "running" })).watchHref
    ).toBeNull();
  });

  it("maps a workflow audit row onto the workflow's run page, cancelling by its guid", () => {
    const r = fromRunAuditItem(
      auditRun("w1", {
        kind: "workflow",
        workflowSlug: "nightly-sync",
        status: "running",
        outcome: "running",
      })
    );
    expect(r).toMatchObject({
      kind: "workflow",
      href: "/workflows/nightly-sync/runs/w1",
      cancel: { kind: "workflow", guid: "w1" },
      retry: null,
    });
  });

  it("maps a task audit row, and drops an unknown trigger", () => {
    const r = fromRunAuditItem(
      auditRun("t1", { kind: "task", appSlug: "billing", trigger: "unknown" })
    );
    expect(r).toMatchObject({ kind: "task", app: "billing", href: "/tasks/runs/t1", trigger: "" });
    expect(r.retry).toBeNull();
  });

  it("maps an agent task on its tab, with how it started and who", () => {
    expect(fromAgentTask(agentTask("a1"))).toMatchObject({
      outcome: "succeeded",
      trigger: "manual",
      startedByMe: true,
      startedBy: "you",
      retry: { id: "a1" },
    });
    const live = fromAgentTask(
      agentTask("a2", { status: "running", finishedAt: null, vncEnabled: true, vncUrl: "/vnc/a2" })
    );
    expect(live.cancel).toEqual({ kind: "agent", id: "a2" });
    expect(live.watchHref).toBe("/agents/runs/a2/vnc");
    expect(fromAgentTask(agentTask("a3", { status: "provisioning" })).outcome).toBe("waiting");
  });

  it("maps an upcoming firing to the agent, never cancellable or retryable", () => {
    const r = fromUpcomingRun(UPCOMING_RUNS[0]!);
    expect(r).toMatchObject({
      upcoming: true,
      id: "0 * * * *",
      trigger: "schedule",
      outcome: "waiting",
      href: "/agents/hourly-digest",
      cancel: null,
      retry: null,
    });
  });

  it("maps a task run for its page, and a definition run for a workflow's tab", () => {
    const r = fromTaskRun(taskRun("t1", { triggeredByUsername: "leo" }), "leo");
    expect(r).toMatchObject({ kind: "task", app: "billing", startedByMe: true, cancel: null });
    expect(fromTaskRun(taskRun("t3", { status: "pending" }), null).outcome).toBe("waiting");
    expect(fromWorkflowRun(workflowRun("w1", { status: "running", endedAt: null }))).toMatchObject({
      outcome: "running",
      cancel: { kind: "workflow", workflowId: "wf-w1" },
    });
  });

  it("reads each kind's status word as an outcome", () => {
    expect(["timed_out", "completed", "queued", "canceled", "mystery"].map(statusOutcome)).toEqual([
      "failed",
      "succeeded",
      "waiting",
      "cancelled",
      "unknown",
    ]);
  });
});

describe("agentRunCrumbs", () => {
  it("names the agent from the task, linking to it, then the run", () => {
    const crumbs = agentRunCrumbs(
      { agentSlug: "bdr-outreach", agentName: "BDR outreach" },
      { label: "run 7f22c1d3" }
    );
    expect(crumbs.slice(1)).toEqual([
      { label: "Runs", href: "/tasks" },
      { label: "BDR outreach", href: "/agents/bdr-outreach" },
      { label: "run 7f22c1d3" },
    ]);
    expect(agentRunCrumbs(null, { label: "run x" })).toHaveLength(3);
  });
});

describe("runAuditVariables", () => {
  it("holds the audit to the Agents area's kinds, newest first", () => {
    expect(runAuditVariables(stateOf(RUNS_LIST, ""), RUN_KINDS, NOW)).toEqual({
      filter: { kind: ["agent", "workflow", "task"] },
      search: null,
      sort: "-at",
      first: 25,
      after: null,
    });
  });

  it("sends the views and chips as the audit's filter", () => {
    const v = runAuditVariables(
      stateOf(
        RUNS_LIST,
        "view=failed&agent=support-bot&project=sales&trigger=schedule&since=24h&q=%20abc%20&after=c1"
      ),
      RUN_KINDS,
      NOW
    );
    expect(v).toEqual({
      filter: {
        kind: ["agent", "workflow", "task"],
        outcome: ["failed"],
        agent: ["support-bot"],
        project: ["sales"],
        trigger: ["schedule"],
        since: new Date(NOW - 24 * 3600 * 1000).toISOString(),
      },
      search: "abc",
      sort: "-at",
      first: 25,
      after: "c1",
    });
    expect(runAuditVariables(stateOf(RUNS_LIST, "view=mine"), RUN_KINDS, NOW)?.filter).toEqual({
      kind: ["agent", "workflow", "task"],
      startedBy: ["me"],
    });
    expect(runAuditVariables(stateOf(RUNS_LIST, "sort=at"), RUN_KINDS, NOW)?.sort).toBe("at");
  });

  it("asks only for the kinds the viewer's modules show, and nothing for a hidden one", () => {
    expect(
      runAuditVariables(stateOf(RUNS_LIST, "kind=workflow"), ["agent", "workflow"], NOW)?.filter
    ).toEqual({ kind: ["workflow"] });
    expect(runAuditVariables(stateOf(RUNS_LIST, "kind=task"), ["agent"], NOW)).toBeNull();
  });
});

describe("agentTasksVariables", () => {
  it("sends the tab's status as the agent task statuses behind it, and Mine as startedByMe", () => {
    const v = agentTasksVariables("org-1", "wl-1", stateOf(AGENT_RUNS_LIST, "view=waiting"));
    expect(v).toEqual({
      orgId: "org-1",
      workloadId: "wl-1",
      search: null,
      filter: { status: ["draft", "queued", "provisioning"] },
      sort: "-created",
      limit: 25,
      after: null,
    });
    expect(
      agentTasksVariables("org-1", "wl-1", stateOf(AGENT_RUNS_LIST, "view=mine&trigger=api")).filter
    ).toEqual({ trigger: ["api"], startedByMe: true });
    expect(
      agentTasksVariables("org-1", "wl-1", stateOf(AGENT_RUNS_LIST, "status=failed")).filter
    ).toEqual({ status: ["failed", "timed_out"] });
  });
});

describe("upcomingVariables", () => {
  it("pages the firings by number behind the list's cursor", () => {
    const s = stateOf(RUNS_LIST, "view=scheduled&agent=hourly-digest&after=p:3");
    expect(upcomingVariables("org-1", s)).toEqual({
      orgId: "org-1",
      search: null,
      agent: ["hourly-digest"],
      project: null,
      perAgent: 3,
      page: 3,
      pageSize: 25,
    });
    expect(upcomingNextCursor(1, 25, 60)).toBe("p:2");
    expect(upcomingNextCursor(3, 25, 60)).toBeNull();
  });

  it("asks nothing when a chip only a past run can match", () => {
    for (const qs of ["kind=workflow", "workflow=nightly", "trigger=api", "status=failed"]) {
      expect(upcomingVariables("org-1", stateOf(RUNS_LIST, `view=scheduled&${qs}`))).toBeNull();
    }
    expect(
      upcomingVariables("org-1", stateOf(RUNS_LIST, "view=scheduled&status=waiting"))
    ).not.toBeNull();
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
