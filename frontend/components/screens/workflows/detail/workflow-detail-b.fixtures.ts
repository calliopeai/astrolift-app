import type {
  ConfiguredWorkflowWithRuns,
  TieredWorkflowRun,
} from "@/graphql/workflows/tiered.types";
import type { WorkflowInstanceDetail } from "@/graphql/workflows/workflows.types";

import type { RunTimelinePanelViewProps } from "./RunTimelinePanel";
import type { WorkflowRunState } from "./use-workflow-run";
import type { WorkflowDetailShellViewProps } from "./WorkflowDetailShell";
import type { WorkflowObserveViewProps } from "./WorkflowObserve";
import type { WorkflowTabsViewProps } from "./WorkflowTabs";

/**
 * Hand-typed fixtures for the workflow detail shell, pillar bar, Run pillar
 * and Observe pillar (group workflow-detail-b).
 */

/** The generated JSON scalar is typed Record<string, unknown>; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const noop = () => {};
const noopAsync = async () => {};

export const LONG =
  "platform-team-shared-production-nightly-reconciliation-workflow-with-a-deliberately-long-name-that-keeps-going";

const MINUTES_AGO = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

export const RUN_RUNNING: TieredWorkflowRun = {
  guid: "3f1c9a52-7d44-4b0e-9a1e-2c8f6b1d0a11",
  currentState: "running",
  temporalWorkflowId: "wf-nightly-sync-20260928-0400",
  temporalRunId: "run-7a8b9c",
  startedAt: MINUTES_AGO(3),
  completedAt: null,
  isCompleted: false,
};

export const RUN_COMPLETED: TieredWorkflowRun = {
  guid: "8b2d4e61-1a3f-4c7d-8e2b-5f6a7c8d9e02",
  currentState: "completed",
  temporalWorkflowId: "wf-nightly-sync-20260927-0400",
  temporalRunId: "run-1d2e3f",
  startedAt: MINUTES_AGO(24 * 60 + 5),
  completedAt: MINUTES_AGO(24 * 60),
  isCompleted: true,
};

export const RUN_FAILED: TieredWorkflowRun = {
  guid: "c4e5f6a7-2b3c-4d5e-9f01-a2b3c4d5e6f7",
  currentState: "failed",
  temporalWorkflowId: null,
  temporalRunId: null,
  startedAt: MINUTES_AGO(2 * 24 * 60),
  completedAt: MINUTES_AGO(2 * 24 * 60 - 1),
  isCompleted: true,
};

export const WORKFLOW: ConfiguredWorkflowWithRuns = {
  guid: "wf-guid-1",
  name: "Nightly sync",
  slug: "nightly-sync",
  description: "Reconciles the CRM with the warehouse every night.",
  triggerKind: "schedule",
  scheduleCron: "0 4 * * *",
  isEnabled: true,
  inputs: json({}),
  stageBindings: json({}),
  organizationGuid: "org-1",
  definitionSlug: "crm-reconcile",
  definitionName: "CRM reconcile",
  patternKind: "sequential",
  runCount: 3,
  createdAt: MINUTES_AGO(30 * 24 * 60),
  runs: [RUN_COMPLETED, RUN_RUNNING, RUN_FAILED],
};

export const DISABLED_WORKFLOW: ConfiguredWorkflowWithRuns = {
  ...WORKFLOW,
  triggerKind: "manual",
  scheduleCron: null,
  isEnabled: false,
  runCount: 0,
  runs: [],
};

export const LONG_WORKFLOW: ConfiguredWorkflowWithRuns = {
  ...WORKFLOW,
  name: LONG,
  slug: LONG,
  definitionName: `${LONG}-definition`,
  triggerKind: "github_pull_request_opened_on_default_branch",
  scheduleCron: "*/15 0-6,18-23 1-7,15-21 1,3,5,7,9,11 MON-FRI",
  runCount: 1,
  runs: [{ ...RUN_RUNNING, temporalWorkflowId: `${LONG}-temporal-workflow-id` }],
};

// ─── Shell + tabs ─────────────────────────────────────────────────────────

export const TABS: WorkflowTabsViewProps = {
  workflowSlug: WORKFLOW.slug,
  pathname: `/workflows/${WORKFLOW.slug}/run`,
};

export const SHELL: WorkflowDetailShellViewProps = {
  workflowSlug: WORKFLOW.slug,
  workflow: WORKFLOW,
  loading: false,
  error: null,
};

// ─── Run pillar ───────────────────────────────────────────────────────────

export const RUN: WorkflowRunState = {
  workflow: WORKFLOW,
  latest: RUN_RUNNING,
  canRun: true,
  canManage: true,
  running: false,
  toggling: false,
  onRun: noopAsync,
  onToggle: noopAsync,
};

// ─── Observe pillar ───────────────────────────────────────────────────────

const SORTED_RUNS = [RUN_RUNNING, RUN_COMPLETED, RUN_FAILED];

export const OBSERVE: Omit<WorkflowObserveViewProps, "timeline"> = {
  runs: SORTED_RUNS,
  loading: false,
  error: null,
  focusGuid: RUN_RUNNING.guid,
  onSelect: noop,
  onRefresh: noop,
};

export const INSTANCE_DETAIL: WorkflowInstanceDetail = {
  instance: {
    workflowId: RUN_RUNNING.temporalWorkflowId ?? "",
    workflowType: "TieredWorkflow",
    runId: RUN_RUNNING.temporalRunId ?? "",
    status: "RUNNING",
    startedAt: RUN_RUNNING.startedAt,
    closedAt: "",
    durationSeconds: null,
    taskQueue: "astrolift-workflows",
    triggeredBy: "schedule",
  },
  history: [
    {
      eventType: "WorkflowExecutionStarted",
      timestamp: MINUTES_AGO(3),
      payload: json({}),
      retryCount: 0,
      decision: "",
    },
    {
      eventType: "ActivityTaskScheduled",
      timestamp: MINUTES_AGO(3),
      payload: json({ stage: 1 }),
      retryCount: 0,
      decision: "",
    },
    {
      eventType: "ActivityTaskFailed",
      timestamp: MINUTES_AGO(2),
      payload: json({ stage: 1 }),
      retryCount: 2,
      decision: "failed",
    },
    {
      eventType: "ActivityTaskCompleted",
      timestamp: MINUTES_AGO(1),
      payload: json({ stage: 1 }),
      retryCount: 3,
      decision: "completed",
    },
    {
      eventType: "ActivityTaskCancelRequested",
      timestamp: MINUTES_AGO(1),
      payload: json(null),
      retryCount: 0,
      decision: "cancelled",
    },
  ],
};

export const LONG_INSTANCE_DETAIL: WorkflowInstanceDetail = {
  ...INSTANCE_DETAIL,
  history: [
    ...INSTANCE_DETAIL.history,
    {
      eventType: `${LONG}-ActivityTaskTimedOutWithAnExceptionallyLongEventTypeName`,
      timestamp: MINUTES_AGO(0),
      payload: json([]),
      retryCount: 12,
      decision: "timed_out",
    },
  ],
};

export const TIMELINE: RunTimelinePanelViewProps = {
  run: RUN_RUNNING,
  detail: INSTANCE_DETAIL,
  loading: false,
  error: null,
  onClose: noop,
};
