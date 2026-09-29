import { STAGES } from "@/components/workflows/fixtures";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
  WorkflowStageExecution,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";
import type { WorkflowInstanceDetail } from "@/graphql/workflows/workflows.types";

import type {
  DefinitionObserveViewProps,
  DefinitionRunPanelViewProps,
  DefinitionRunViewProps,
  DefinitionWorkflowScreenProps,
} from "./DefinitionWorkflow";
import type { GateReviewViewProps } from "./GateReview";
import { buildRunDagStages } from "./run-dag-stages";
import type { WorkflowRunDagViewProps } from "./WorkflowRunDag";

/** Hand-typed fixtures for the definition workflow page, its run graph and gate review. */

const yes = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

const topologyStage = (
  order: number,
  kind: string,
  agentName: string,
  patch: Partial<WorkflowTopologyStage> = {}
): WorkflowTopologyStage => ({
  guid: `topo-${order}`,
  order,
  kind,
  role: "",
  agentRef: agentName,
  workflowRef: "",
  agentGuid: agentName ? `agent-${order}` : null,
  agentName,
  agentSlug: agentName,
  environmentSpecSlug: "default",
  resolvedModel: "claude-sonnet",
  hasPrompt: true,
  outputKey: agentName,
  skillRefs: [],
  fanOutCount: null,
  fanOutDynamic: false,
  onFailure: "fail",
  timeoutSeconds: 600,
  ...patch,
});

export const DEFINITION: WorkflowDefinitionSummary = {
  guid: "def-outbound",
  name: "Outbound pipeline",
  slug: "outbound-pipeline",
  description: "Research accounts in parallel, merge, get sign-off, then send.",
  patternKind: "fan_out_aggregate",
  isEnabled: true,
  isGlobal: false,
  organizationGuid: "org-7f3c2a10",
  projectGuid: "proj-sales",
  projectSlug: "sales-agents",
  projectTeamSlug: "gtm",
  sourceRepo: "calliopeai/sales-agents",
  sourcePath: ".astrolift/workflows/outbound.toml",
  sourceRef: "main@4c1e9a2",
  stageCount: 4,
  stages: [
    topologyStage(0, "agent_dispatch", "account-research", { fanOutCount: 3 }),
    topologyStage(1, "aggregation", ""),
    topologyStage(2, "human_gate", "", { role: "approve" }),
    topologyStage(3, "agent_dispatch", "bdr-outreach"),
  ],
  createdAt: "2026-09-20T10:00:00Z",
};

export const LONG_DEFINITION: WorkflowDefinitionSummary = {
  ...DEFINITION,
  name: `Outbound pipeline for ${LONG}`,
  slug: LONG,
  patternKind: "fan_out_aggregate_with_human_review_and_retry",
  sourceRepo: `calliopeai/${LONG}`,
  sourcePath: `.astrolift/workflows/${LONG}/outbound.toml`,
  sourceRef: `refs/heads/${LONG}@4c1e9a2b7d`,
  stages: DEFINITION.stages.map((s) => ({
    ...s,
    agentName: s.agentName && `${s.agentName}-${LONG}`,
  })),
};

export const SCREEN: DefinitionWorkflowScreenProps = {
  slug: DEFINITION.slug,
  definition: DEFINITION,
  loading: false,
  error: null,
};

const run = (n: number, patch: Partial<WorkflowDefinitionRun> = {}): WorkflowDefinitionRun => ({
  guid: `run-${n}`,
  definitionGuid: DEFINITION.guid,
  definitionSlug: DEFINITION.slug,
  definitionName: DEFINITION.name,
  projectGuid: DEFINITION.projectGuid,
  projectSlug: DEFINITION.projectSlug,
  status: "completed",
  temporalWorkflowId: `outbound-pipeline-2026092${n}-a1b2c3`,
  temporalRunId: `temporal-run-${n}`,
  currentStageOrder: null,
  currentStageRole: "",
  parentRunGuid: null,
  parentStageExecutionGuid: null,
  nestingDepth: 0,
  childRunCount: 0,
  startedAt: `2026-09-2${n}T14:02:00Z`,
  endedAt: `2026-09-2${n}T14:09:41Z`,
  ...patch,
});

export const RUNNING_RUN = run(8, {
  status: "running",
  currentStageOrder: 2,
  currentStageRole: "approve",
  endedAt: null,
  childRunCount: 2,
});
export const RUNS: WorkflowDefinitionRun[] = [
  RUNNING_RUN,
  run(7),
  run(6, { status: "failed" }),
  run(5, { status: "cancelled", parentRunGuid: "run-parent-1", nestingDepth: 1 }),
];

export const RUN: DefinitionRunViewProps = {
  definition: DEFINITION,
  canRun: true,
  running: false,
  latest: RUNNING_RUN,
  run: yes,
};

export const OBSERVE: Omit<DefinitionObserveViewProps, "renderRunPanel"> = {
  runs: RUNS,
  loading: false,
  error: undefined,
  refetch: (async () => ({})) as unknown as DefinitionObserveViewProps["refetch"],
  requestedRunGuid: null,
};

export const DETAIL: WorkflowInstanceDetail = {
  instance: {
    workflowId: RUNNING_RUN.temporalWorkflowId,
    workflowType: "DefinitionWorkflow",
    runId: "temporal-run-8",
    status: "RUNNING",
    startedAt: "2026-09-28T14:02:00Z",
    closedAt: "",
    durationSeconds: null,
    taskQueue: "astrolift-workflows",
    triggeredBy: "leo",
  },
  history: [
    "WorkflowExecutionStarted",
    "ActivityTaskScheduled",
    "ActivityTaskCompleted",
    "ChildWorkflowExecutionStarted",
    "ChildWorkflowExecutionCompleted",
    "SignalExternalWorkflowExecutionInitiated",
  ].map((eventType, i) => ({
    eventType,
    timestamp: `2026-09-28T14:0${2 + i}:00Z`,
    payload: {},
    retryCount: 0,
    decision: "",
  })),
};

export const PANEL: Omit<DefinitionRunPanelViewProps, "runDag"> = {
  run: RUNNING_RUN,
  canRun: true,
  terminal: false,
  detail: DETAIL,
  detailLoading: false,
  detailError: null,
  cancelling: false,
  cancel: yes,
};

const execution = (
  guid: string,
  stageOrder: number,
  stageKind: string,
  patch: Partial<WorkflowStageExecution> = {}
): WorkflowStageExecution => ({
  guid,
  status: "completed",
  attemptNumber: 1,
  startedAt: "2026-09-28T14:02:00Z",
  endedAt: "2026-09-28T14:04:00Z",
  output: null,
  failure: null,
  errorMessage: "",
  createdAt: "2026-09-28T14:02:00Z",
  executionId: String(10 + stageOrder),
  stageGuid: `stage-${stageOrder}`,
  stageKind,
  stageOrder,
  agentRunGuid: null,
  childWorkflowRunGuid: null,
  childWorkflowDefinitionSlug: null,
  childWorkflowStatus: null,
  ...patch,
});

export const EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-research-1", 0, "agent_dispatch", { executionId: "10" }),
  execution("x-research-2", 0, "agent_dispatch", { executionId: "11" }),
  execution("x-research-3", 0, "agent_dispatch", { executionId: "12" }),
  execution("x-merge", 1, "aggregation", {
    output: {
      result:
        "Draft reply to Acme Corp: thanks for the call on Tuesday, here is the pricing sheet we discussed.",
    },
  }),
  execution("x-gate", 2, "human_gate", {
    status: "running",
    endedAt: null,
    executionId: "72",
    humanGateState: "pending",
    stageRole: "outreach review",
    stageApprovers: ["team:gtm", "sales-lead@example.com"],
  }),
];

export const GATES: GateReviewViewProps = {
  workflowId: RUNNING_RUN.temporalWorkflowId,
  executions: EXECUTIONS,
  decide: yes,
  decidingGuids: [],
};

export const LONG_EXECUTIONS: WorkflowStageExecution[] = [
  execution("x-long-merge", 1, "aggregation", {
    output: { result: `${LONG}\n\n`.repeat(12), extra: { nested: [LONG, LONG] } },
  }),
  execution("x-long-gate", 2, "human_gate", {
    status: "running",
    executionId: "73",
    humanGateState: "pending",
    stageRole: `outreach review for ${LONG}`,
    stageApprovers: [`team:${LONG}`, `${LONG}@example.com`],
  }),
];

export const DAG: WorkflowRunDagViewProps = {
  loading: false,
  dagStages: buildRunDagStages(STAGES, EXECUTIONS),
  signature: "full",
};

/** Planned stages only: the run has not reported any execution yet. */
export const PLANNED_DAG: WorkflowRunDagViewProps = {
  loading: false,
  dagStages: buildRunDagStages(STAGES, []),
  signature: "planned",
};

export const LONG_DAG: WorkflowRunDagViewProps = {
  loading: false,
  dagStages: buildRunDagStages(
    STAGES.map((s) => ({
      ...s,
      agentDefinitionName: s.agentDefinitionName && `${s.agentDefinitionName}-${LONG}`,
    })),
    EXECUTIONS
  ),
  signature: "long",
};
