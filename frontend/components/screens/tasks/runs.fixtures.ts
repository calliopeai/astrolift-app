import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";
import type { AstroliftAgentUpcomingRun } from "@/graphql/agents/agents.types";

import {
  COMPLETED_TASK,
  FAILED_TASK,
  RUNNING_TASK,
} from "@/components/screens/agents/runs/agent-runs.fixtures";

import {
  fromAgentTask,
  fromRunAuditItem,
  fromUpcomingRun,
  type RunAuditSource,
  type RunRow,
} from "./runs-list";
import type { RunsScreenProps } from "./RunsScreen";

const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();
const soon = (seconds: number) => new Date(Date.now() + seconds * 1000).toISOString();

export function workflowRun(
  guid: string,
  patch: Partial<WorkflowDefinitionRun> = {}
): WorkflowDefinitionRun {
  return {
    guid,
    definitionGuid: "def-1",
    definitionSlug: "nightly-sync",
    definitionName: "Nightly sync",
    projectGuid: "proj-1",
    projectSlug: "sales",
    status: "completed",
    temporalWorkflowId: `wf-${guid}`,
    temporalRunId: `run-${guid}`,
    currentStageOrder: 3,
    currentStageRole: "publish",
    parentRunGuid: null,
    parentStageExecutionGuid: null,
    nestingDepth: 0,
    childRunCount: 0,
    startedAt: ago(3600),
    endedAt: ago(3300),
    ...patch,
  };
}

/** One row of `astroliftRunAudit`, as the Runs page reads it. */
export function auditRun(id: string, patch: Partial<RunAuditSource> = {}): RunAuditSource {
  return {
    kind: "agent",
    id,
    subject: "bdr-outreach",
    agentSlug: "bdr-outreach",
    workflowSlug: "",
    projectSlug: "sales",
    appSlug: "",
    trigger: "manual",
    startedByDisplay: "Leo Mata",
    startedByMe: true,
    at: ago(600),
    startedAt: ago(600),
    endedAt: ago(540),
    durationSeconds: 60,
    status: "completed",
    outcome: "succeeded",
    ...patch,
  };
}

/** An agent's own tasks, as its Runs tab reads them from `agentTasksPage`. */
export const AGENT_RUN_ROWS: RunRow[] = [
  fromAgentTask({ ...RUNNING_TASK, triggerKind: "manual", triggeredByMe: true }),
  fromAgentTask({
    ...COMPLETED_TASK,
    id: "6e11b0c2-3a4f-4e5d-8c9b-1a2b3c4d5e6f",
    triggerKind: "schedule",
    triggeredByMe: false,
  }),
  fromAgentTask({
    ...FAILED_TASK,
    id: "5d00a9b1-293e-4d4c-9b8a-0f1e2d3c4b5a",
    startedAt: ago(900),
    triggerKind: "api",
    triggeredByMe: false,
  }),
  fromAgentTask({
    ...RUNNING_TASK,
    id: "4cff98a0-182d-4c3b-8a79-fe0d1c2b3a49",
    status: "queued",
    startedAt: null,
    createdAt: ago(20),
    vncEnabled: false,
    vncUrl: "",
    triggerKind: "manual",
    triggeredByMe: true,
  }),
];

/** The Runs page's first page: agent, workflow and task runs, newest first. */
export const RUN_ROWS: RunRow[] = [
  fromRunAuditItem(
    auditRun("7f22c1d3-4b5a-4f6e-9d0c-2b3c4d5e6f70", {
      status: "running",
      outcome: "running",
      at: ago(40),
      startedAt: ago(40),
      endedAt: null,
      durationSeconds: null,
    }),
    { vncEnabled: true, vncUrl: "/vnc/7f22c1d3" }
  ),
  fromRunAuditItem(
    auditRun("wf-0002", {
      kind: "workflow",
      subject: "Nightly sync",
      agentSlug: "",
      workflowSlug: "nightly-sync",
      trigger: "schedule",
      startedByDisplay: "",
      startedByMe: false,
      status: "running",
      outcome: "running",
      at: ago(120),
      startedAt: ago(120),
      endedAt: null,
      durationSeconds: null,
    })
  ),
  fromRunAuditItem(
    auditRun("4cff98a0-182d-4c3b-8a79-fe0d1c2b3a49", {
      status: "queued",
      outcome: "waiting",
      at: ago(200),
      startedAt: null,
      endedAt: null,
      durationSeconds: null,
    })
  ),
  fromRunAuditItem(auditRun("6e11b0c2-3a4f-4e5d-8c9b-1a2b3c4d5e6f")),
  fromRunAuditItem(
    auditRun("5d00a9b1-293e-4d4c-9b8a-0f1e2d3c4b5a", {
      status: "timed_out",
      outcome: "failed",
      trigger: "api",
      startedByDisplay: "",
      startedByMe: false,
      at: ago(900),
      startedAt: ago(900),
      endedAt: ago(300),
      durationSeconds: 600,
    })
  ),
  fromRunAuditItem(
    auditRun("wf-0003", {
      kind: "workflow",
      subject: "Enrich accounts",
      agentSlug: "",
      workflowSlug: "enrich-accounts",
      trigger: "parent",
      startedByDisplay: "",
      startedByMe: false,
      status: "failed",
      outcome: "failed",
      at: ago(3600),
      startedAt: ago(3600),
      endedAt: ago(3300),
      durationSeconds: 300,
    })
  ),
  fromRunAuditItem(
    auditRun("c0ffee00-1111-4222-8333-444455556666", {
      kind: "task",
      subject: "billing / migrate",
      agentSlug: "",
      projectSlug: "",
      appSlug: "billing",
      trigger: "unknown",
      startedByDisplay: "",
      startedByMe: false,
      status: "succeeded",
      outcome: "succeeded",
      at: ago(7200),
      startedAt: ago(7200),
      endedAt: ago(7140),
      durationSeconds: 60,
    })
  ),
];

/** The Scheduled view: the next firings of the scheduled agents, soonest first. */
export const UPCOMING_RUNS: AstroliftAgentUpcomingRun[] = [
  {
    agentId: "wl-digest",
    agentSlug: "hourly-digest",
    agentName: "Hourly digest",
    appSlug: "support",
    projectSlug: "platform",
    cronExpression: "0 * * * *",
    scheduledAt: soon(900),
  },
  {
    agentId: "wl-digest",
    agentSlug: "hourly-digest",
    agentName: "Hourly digest",
    appSlug: "support",
    projectSlug: "platform",
    cronExpression: "0 * * * *",
    scheduledAt: soon(4500),
  },
  {
    agentId: "wl-nightly",
    agentSlug: "nightly-report",
    agentName: "Nightly report",
    appSlug: "reports",
    projectSlug: "platform",
    cronExpression: "0 3 * * *",
    scheduledAt: soon(40_000),
  },
];

export const UPCOMING_ROWS: RunRow[] = UPCOMING_RUNS.map(fromUpcomingRun);

const LONG = "a-very-long-identifier-that-keeps-going-to-show-how-the-layout-wraps".repeat(3);

export const LONG_RUN_ROWS: RunRow[] = [
  fromRunAuditItem(
    auditRun("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", {
      subject: LONG,
      agentSlug: LONG,
      projectSlug: `project-${LONG}`,
      startedByDisplay: `a.person.with.a.very.long.name.${LONG}`,
      status: "running",
      outcome: "running",
      endedAt: null,
      durationSeconds: null,
    })
  ),
  fromRunAuditItem(
    auditRun("arn:aws:states:us-east-2:316992255370:execution:" + LONG, {
      kind: "workflow",
      subject: `https://hooks.example.com/${LONG}`,
      agentSlug: "",
      workflowSlug: LONG,
      projectSlug: LONG,
    })
  ),
  fromUpcomingRun({
    ...UPCOMING_RUNS[0]!,
    agentSlug: LONG,
    cronExpression: "0 0,6,12,18 1-7,15-21 1,4,7,10 MON-FRI",
  }),
];

const noop = () => {};

export const RUNS_SCREEN: Omit<RunsScreenProps, "list"> = {
  rows: RUN_ROWS,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: "cursor-2",
  totalCount: 42,
  approximateCount: false,
  lookup: (key) =>
    [...RUN_ROWS, ...AGENT_RUN_ROWS, ...LONG_RUN_ROWS].find((r) => r.key === key) ?? null,
  onCancel: async () => {},
  onRetryRuns: async () => {},
};
