import { fromWorkflowRun } from "@/components/screens/tasks/runs-list";
import type {
  WorkflowStageExecution,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";
import type { WorkflowHistoryEvent } from "@/graphql/workflows/workflows.types";

import { DEFINITION, DETAIL, RUNNING_RUN, RUNS } from "./workflow-detail-a.fixtures";
import { RUN_COMPLETED, RUN_FAILED, RUN_RUNNING, WORKFLOW } from "./workflow-detail-b.fixtures";
import {
  definitionRunSubject,
  liveRunsSnapshot,
  type PlanStage,
  type WorkflowRunSubject,
} from "./workflow-run-model";
import { fromConfiguredRun } from "./workflow-runs-list";
import type { WorkflowRunScreenProps } from "./WorkflowRunScreen";
import type { WorkflowRunsTabProps } from "./WorkflowRunsTab";

/**
 * Hand-typed fixtures for a workflow's Runs tab and one run's page: a
 * fan-out that waits at a gate, a run that looped back twice, a failed
 * branch, and the long strings.
 */

export const NOW = Date.UTC(2026, 8, 28, 14, 8, 0);
const at = (min: number, sec = 0) => new Date(Date.UTC(2026, 8, 28, 14, min, sec)).toISOString();

export const SHA = "4c1e9a2b7d3f5e6a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a";
export const ARN = `arn:aws:states:us-west-2:718519534729:execution:platform-team-shared-production-workloads-nightly-reconciliation:${"x".repeat(80)}`;
export const URL_UNBROKEN = `https://hooks.example.com/workflows/outbound-pipeline/runs/${"a1b2c3d4".repeat(12)}?token=${"z".repeat(40)}`;

const noop = () => {};
const yes = async () => true;

export const execution = (
  guid: string,
  stageOrder: number,
  stageKind: string,
  patch: Partial<WorkflowStageExecution> = {}
): WorkflowStageExecution => ({
  guid,
  status: "completed",
  attemptNumber: 1,
  startedAt: at(2),
  endedAt: at(3),
  output: null,
  failure: null,
  errorMessage: "",
  createdAt: at(2),
  executionId: guid.replace(/\D/g, "") || "1",
  stageGuid: `stage-${stageOrder}`,
  stageKind,
  stageOrder,
  agentRunGuid: null,
  childWorkflowRunGuid: null,
  childWorkflowDefinitionSlug: null,
  childWorkflowStatus: null,
  ...patch,
});

// ─── A fan-out, merged, waiting at the gate ──────────────────────────────

/** Research ×3, merge, gate, outreach (the outbound pipeline's topology). */
export const PLAN: PlanStage[] = DEFINITION.stages;

export const RUN: WorkflowRunSubject = {
  ...definitionRunSubject(RUNNING_RUN),
  startedAt: at(2),
};

export const GATE_EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-research-1", 0, "agent_dispatch", {
    executionId: "10",
    startedAt: at(2),
    endedAt: at(4, 10),
  }),
  execution("x-research-2", 0, "agent_dispatch", {
    executionId: "11",
    startedAt: at(2, 5),
    endedAt: at(5),
  }),
  execution("x-research-3", 0, "agent_dispatch", {
    executionId: "12",
    startedAt: at(2, 10),
    endedAt: at(3, 30),
  }),
  execution("x-merge", 1, "aggregation", {
    executionId: "13",
    startedAt: at(5),
    endedAt: at(6),
    output: {
      result:
        "Draft reply to Acme Corp: thanks for the call on Tuesday, here is the pricing sheet we discussed.",
    },
  }),
  execution("x-gate", 2, "human_gate", {
    executionId: "72",
    status: "running",
    startedAt: at(6),
    endedAt: null,
    humanGateState: "pending",
    stageRole: "outreach review",
    stageApprovers: ["team:gtm", "sales-lead@example.com"],
  }),
];

export const HISTORY: WorkflowHistoryEvent[] = DETAIL.history;

export const RUN_SCREEN: WorkflowRunScreenProps = {
  slug: DEFINITION.slug,
  workflowName: DEFINITION.name,
  runId: RUNNING_RUN.guid,
  run: RUN,
  loading: false,
  error: null,
  onRetry: noop,
  windowed: true,
  plan: PLAN,
  executions: GATE_EXECUTIONS,
  executionsLoading: false,
  executionsError: null,
  onRetryExecutions: noop,
  history: HISTORY,
  historyLoading: false,
  historyError: null,
  onRetryHistory: noop,
  onDownloadLog: noop,
  now: NOW,
  motion: "reduced",
  viewerGateIds: ["72"],
  decide: yes,
  decidingGuids: [],
  canCancel: true,
  cancelling: false,
  onCancel: yes,
};

// ─── A run that looped: Test failed, back to Code, then passed ───────────

const loopStage = (
  order: number,
  kind: string,
  agentName: string,
  role = ""
): WorkflowTopologyStage => ({
  ...DEFINITION.stages[3]!,
  guid: `loop-${order}`,
  order,
  kind,
  role,
  agentName,
  agentRef: agentName,
  agentSlug: agentName,
  fanOutCount: null,
  fanOutDynamic: false,
});

export const LOOP_PLAN: PlanStage[] = [
  loopStage(0, "agent_dispatch", "coder"),
  loopStage(1, "agent_dispatch", "tester"),
  loopStage(2, "human_gate", "", "code review"),
  loopStage(3, "agent_dispatch", "deployer"),
];

export const LOOP_EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-code-1", 0, "agent_dispatch", {
    executionId: "20",
    startedAt: at(0),
    endedAt: at(1),
  }),
  execution("x-test-1", 1, "agent_dispatch", {
    executionId: "21",
    status: "failed",
    startedAt: at(1),
    endedAt: at(2),
    errorMessage: "3 specs failed in checkout",
  }),
  execution("x-code-2", 0, "agent_dispatch", {
    executionId: "22",
    startedAt: at(2),
    endedAt: at(3),
  }),
  execution("x-test-2", 1, "agent_dispatch", {
    executionId: "23",
    startedAt: at(3),
    endedAt: at(4),
  }),
  execution("x-review", 2, "human_gate", {
    executionId: "24",
    status: "approved",
    startedAt: at(4),
    endedAt: at(5),
    stageRole: "code review",
  }),
  execution("x-deploy", 3, "agent_dispatch", {
    executionId: "25",
    startedAt: at(5),
    endedAt: at(7),
  }),
];

export const LOOP_RUN: WorkflowRunSubject = {
  ...definitionRunSubject(RUNS[1]!),
  guid: "run-loop-5310",
  status: "completed",
  outcome: "succeeded",
  live: false,
  startedAt: at(0),
  endedAt: at(7),
};

// ─── A branch failed, so the run failed ──────────────────────────────────

export const FAILED_EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-research-1", 0, "agent_dispatch", {
    executionId: "30",
    startedAt: at(2),
    endedAt: at(3),
  }),
  execution("x-research-2", 0, "agent_dispatch", {
    executionId: "31",
    status: "failed",
    startedAt: at(2),
    endedAt: at(2, 40),
    errorMessage: "Enrichment API returned 429 Too Many Requests",
  }),
  execution("x-research-3", 0, "agent_dispatch", {
    executionId: "32",
    startedAt: at(2),
    endedAt: at(4),
  }),
];

export const FAILED_RUN: WorkflowRunSubject = {
  ...definitionRunSubject(RUNS[2]!),
  startedAt: at(2),
  endedAt: at(4),
};

// ─── Long strings ────────────────────────────────────────────────────────

export const LONG_EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-long-1", 0, "agent_dispatch", {
    executionId: "40",
    status: "failed",
    startedAt: at(2),
    endedAt: at(3),
    errorMessage: `Could not assume ${ARN} while fetching ${URL_UNBROKEN} at commit ${SHA}`,
  }),
  execution("x-long-2", 0, "agent_dispatch", {
    executionId: "41",
    startedAt: at(2),
    endedAt: at(4),
  }),
  execution("x-long-gate", 2, "human_gate", {
    executionId: "73",
    status: "running",
    startedAt: at(4),
    endedAt: null,
    humanGateState: "pending",
    stageRole: `outreach review for ${ARN}`,
    stageApprovers: [`team:${SHA}`, "sales-lead@example.com"],
  }),
];

export const LONG_PLAN: PlanStage[] = PLAN.map((s) => ({
  ...s,
  agentName: s.agentName && `${s.agentName}-${SHA}`,
}));

// ─── The Runs tab ────────────────────────────────────────────────────────

export const DEFINITION_ROWS = RUNS.map(fromWorkflowRun);

export const CONFIGURED_ROWS = [RUN_RUNNING, RUN_COMPLETED, RUN_FAILED].map((r) =>
  fromConfiguredRun(r, WORKFLOW)
);

export const LIVE = liveRunsSnapshot(PLAN, RUNS.map(definitionRunSubject), {
  lineId: DEFINITION.guid,
  name: DEFINITION.name,
  now: NOW,
});

export const RUNS_TAB: Omit<WorkflowRunsTabProps, "list"> = {
  rows: DEFINITION_ROWS,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: null,
  totalCount: DEFINITION_ROWS.length,
  approximateCount: false,
  coverage: null,
  live: LIVE,
};
