import type {
  ConfiguredWorkflowWithRuns,
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";
import type { WorkflowInstance, WorkflowInstanceDetail } from "@/graphql/workflows/workflows.types";

import type {
  InstanceAdminControlsViewProps,
  InstanceDetailViewProps,
} from "./WorkflowInstancesPanel";
import { joinWorkflows, type WorkflowRow } from "./workflows-list";
import type { WorkflowsListScreenProps } from "./WorkflowsListScreen";

/** Hand-typed fixtures for /workflows and the platform instances page. */

const noop = () => {};
const asyncNoop = async () => {};
// The generated JSON scalar is typed Record<string, unknown>; real values are any JSON.
const json = (value: unknown) => value as Record<string, unknown>;

export const LONG =
  "platform-team-shared-production-emr-triage-intake-and-decision-pipeline-with-a-deliberately-long-name-that-keeps-going";

/** A 64-char SHA, a 200-char ARN and an unbroken URL, for the long-strings stories. */
export const SHA64 = "f1f9f11a0c2e4b7d9a3e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4";
export const ARN200 =
  `arn:aws:states:us-west-2:718519534729:stateMachine:${"platform-shared-production-".repeat(6)}pipeline`.slice(
    0,
    200
  );
export const UNBROKEN_URL =
  "https://github.com/calliopeai/astrolift-agents-with-a-very-long-organisation-name/blob/main/workflows/nightly/production/emr-triage-intake-and-decision-pipeline.toml";

export const loadError = (message = "upstream timed out") => new globalThis.Error(message);

// ─── Definitions ─────────────────────────────────────────────────────────────

const stage = (overrides: Partial<WorkflowTopologyStage>): WorkflowTopologyStage => ({
  guid: "stage-1",
  order: 0,
  kind: "agent_dispatch",
  role: "intake",
  agentRef: "emr-triage-intake",
  workflowRef: "",
  agentGuid: "agent-1",
  agentName: "EMR Triage Intake",
  agentSlug: "emr-triage-intake",
  environmentSpecSlug: "emr-triage-intake",
  resolvedModel: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  hasPrompt: true,
  outputKey: "evidence",
  skillRefs: ["jira-search"],
  fanOutCount: null,
  fanOutDynamic: false,
  onFailure: "fail",
  timeoutSeconds: 300,
  ...overrides,
});

const definition = (overrides: Partial<WorkflowDefinitionSummary>): WorkflowDefinitionSummary => ({
  guid: "def-1",
  name: "EMR Triage",
  slug: "emr-triage",
  description: "Triage incoming EMR bug reports and decide on an owner.",
  patternKind: "chained",
  isEnabled: true,
  isGlobal: false,
  organizationGuid: "org-1",
  projectGuid: "project-1",
  projectSlug: "emr-bug-triage",
  projectTeamSlug: "engineering",
  sourceRepo: "steadymd/smd-agents",
  sourcePath: "workflows/emr-triage.toml",
  sourceRef: "main",
  stageCount: 2,
  stages: [
    stage({}),
    stage({
      guid: "stage-2",
      order: 1,
      role: "decide",
      agentRef: "emr-triage-decide",
      agentGuid: "agent-2",
      agentName: "EMR Triage Decide",
      agentSlug: "emr-triage-decide",
      environmentSpecSlug: "emr-triage-decide",
      resolvedModel: "us.anthropic.claude-opus-4-8",
      outputKey: "decision",
      skillRefs: [],
    }),
  ],
  createdAt: "2026-08-13T12:00:00Z",
  ...overrides,
});

export const REPO_WORKFLOW = definition({});

export const TEMPLATES: WorkflowDefinitionSummary[] = [
  definition({
    guid: "def-g1",
    name: "Code review loop",
    slug: "code-review-loop",
    description: "An author agent drafts, a reviewer agent critiques, repeat until approved.",
    patternKind: "review_loop",
    isGlobal: true,
    organizationGuid: null,
    projectGuid: null,
    projectSlug: "",
    projectTeamSlug: "",
    sourceRepo: "",
    sourcePath: "",
    stageCount: 2,
    stages: [
      stage({ guid: "g1-s0", role: "draft", onFailure: "retry" }),
      stage({ guid: "g1-s1", order: 1, kind: "human_gate", role: "review", agentSlug: "" }),
    ],
  }),
  definition({
    guid: "def-g2",
    name: "Research fan-out",
    slug: "research-fan-out",
    description: "Split a question across parallel researchers and merge their findings.",
    patternKind: "fan_out",
    isGlobal: true,
    organizationGuid: null,
    projectGuid: null,
    projectSlug: "",
    projectTeamSlug: "",
    sourceRepo: "",
    sourcePath: "",
    stageCount: 3,
    stages: [
      stage({ guid: "g2-s0", role: "plan" }),
      stage({ guid: "g2-s1", order: 1, role: "research", fanOutCount: 4 }),
      stage({ guid: "g2-s2", order: 2, kind: "aggregation", role: "merge", agentSlug: "" }),
    ],
  }),
  definition({
    guid: "def-g3",
    name: "Single agent",
    slug: "single-agent",
    description: "",
    patternKind: "single",
    isGlobal: true,
    organizationGuid: null,
    projectGuid: null,
    projectSlug: "",
    projectTeamSlug: "",
    sourceRepo: "",
    sourcePath: "",
    stageCount: 1,
    stages: [stage({ guid: "g3-s0", role: "dispatch" })],
  }),
];

export const ORG_DEFINITION = definition({
  guid: "def-o1",
  name: "Nightly drift report",
  slug: "nightly-drift-report",
  description: "",
  patternKind: "chained",
  isEnabled: false,
  projectGuid: null,
  projectSlug: "",
  projectTeamSlug: "",
  sourceRepo: "",
  sourcePath: "",
  stageCount: 2,
  stages: [
    stage({ guid: "o1-s0", role: "collect" }),
    stage({ guid: "o1-s1", order: 1, kind: "workflow", role: "", workflowRef: "emr-triage" }),
  ],
});

export const SUPERVISOR_DEFINITION = definition({
  guid: "def-o2",
  name: "Incident responder",
  slug: "incident-responder",
  description: "A supervisor splits an incident into checks and hands them to workers.",
  patternKind: "supervisor_worker",
  projectSlug: "incident-ops",
  sourceRepo: "",
  sourcePath: "",
  stageCount: 3,
  stages: [
    stage({ guid: "o2-s0", role: "triage" }),
    stage({ guid: "o2-s1", order: 1, role: "route", fanOutCount: 3 }),
    stage({ guid: "o2-s2", order: 2, kind: "aggregation", role: "report", agentSlug: "" }),
  ],
});

export const DEFINITIONS: WorkflowDefinitionSummary[] = [
  REPO_WORKFLOW,
  ORG_DEFINITION,
  SUPERVISOR_DEFINITION,
  ...TEMPLATES,
];

export const LONG_DEFINITION = definition({
  guid: "def-long",
  name: LONG,
  slug: LONG,
  description: `${LONG} ${SHA64} ${UNBROKEN_URL}`,
  patternKind: "supervisor_worker",
  projectSlug: ARN200,
  sourceRepo: UNBROKEN_URL,
  projectTeamSlug: "platform-engineering-and-reliability",
  sourcePath: `workflows/${LONG}.toml`,
  stageCount: 9,
  stages: Array.from({ length: 9 }, (_, i) =>
    stage({ guid: `long-s${i}`, order: i, role: `step-${i}`, fanOutCount: i === 2 ? 3 : null })
  ),
});

// ─── Configured workflows ────────────────────────────────────────────────────

export const CONFIGURED: ConfiguredWorkflowWithRuns[] = [
  {
    guid: "wf-1",
    name: "Weekly dependency audit",
    slug: "weekly-dependency-audit",
    description: "Scan every repo for outdated dependencies and open one PR per repo.",
    triggerKind: "schedule",
    scheduleCron: "0 9 * * 1",
    isEnabled: true,
    inputs: json({ repos: ["astrolift-app", "zentinelle"] }),
    stageBindings: json({}),
    organizationGuid: "org-1",
    definitionSlug: "research-fan-out",
    definitionName: "Research fan-out",
    patternKind: "fan_out",
    runCount: 12,
    createdAt: "2026-07-01T10:00:00Z",
    runs: [
      {
        guid: "run-a",
        currentState: "completed",
        temporalWorkflowId: "wf-weekly-dependency-audit-11",
        temporalRunId: null,
        startedAt: "2026-09-21T09:00:00Z",
        completedAt: "2026-09-21T09:12:00Z",
        isCompleted: true,
      },
      {
        guid: "run-b",
        currentState: "failed",
        temporalWorkflowId: "wf-weekly-dependency-audit-12",
        temporalRunId: null,
        startedAt: "2026-09-28T09:00:00Z",
        completedAt: "2026-09-28T09:04:00Z",
        isCompleted: true,
      },
    ],
  },
  {
    guid: "wf-2",
    name: "PR reviewer",
    slug: "pr-reviewer",
    description: "",
    triggerKind: "webhook",
    scheduleCron: null,
    isEnabled: false,
    inputs: json({}),
    stageBindings: json({}),
    organizationGuid: "org-1",
    definitionSlug: "code-review-loop",
    definitionName: "Code review loop",
    patternKind: "review_loop",
    runCount: 0,
    createdAt: "2026-09-02T10:00:00Z",
    runs: [],
  },
];

export const LONG_CONFIGURED: ConfiguredWorkflowWithRuns = {
  ...CONFIGURED[0],
  guid: "wf-long",
  name: LONG,
  slug: LONG,
  description: `${LONG} ${LONG}`,
  triggerKind: "some_custom_trigger_kind",
  definitionName: LONG,
};

// ─── Runs ────────────────────────────────────────────────────────────────────

const definitionRun = (overrides: Partial<WorkflowDefinitionRun>): WorkflowDefinitionRun => ({
  guid: "drun-1",
  definitionGuid: "def-1",
  definitionSlug: "emr-triage",
  definitionName: "EMR Triage",
  projectGuid: "project-1",
  projectSlug: "emr-bug-triage",
  status: "running",
  temporalWorkflowId: "WorkflowDefinitionRunWorkflow-92",
  temporalRunId: "temporal-run-1",
  currentStageOrder: 0,
  currentStageRole: "intake",
  parentRunGuid: null,
  parentStageExecutionGuid: null,
  nestingDepth: 0,
  childRunCount: 0,
  startedAt: "2026-09-28T12:00:00Z",
  endedAt: null,
  ...overrides,
});

export const RUNNING_RUNS: WorkflowDefinitionRun[] = [
  definitionRun({ childRunCount: 2 }),
  definitionRun({
    guid: "drun-2",
    temporalWorkflowId: "WorkflowDefinitionRunWorkflow-93",
    parentRunGuid: "drun-1",
    nestingDepth: 1,
    currentStageOrder: 1,
    currentStageRole: "decide",
  }),
  definitionRun({
    guid: "drun-3",
    status: "pending",
    temporalWorkflowId: "WorkflowDefinitionRunWorkflow-94",
    currentStageOrder: null,
    currentStageRole: "",
    projectSlug: "",
    startedAt: null,
  }),
];

export const HISTORICAL_RUNS: WorkflowDefinitionRun[] = [
  definitionRun({
    guid: "drun-h1",
    startedAt: "2026-09-27T12:00:00Z",
    status: "completed",
    temporalWorkflowId: "WorkflowDefinitionRunWorkflow-80",
    currentStageOrder: 1,
    currentStageRole: "decide",
    endedAt: "2026-09-27T12:04:00Z",
  }),
  definitionRun({
    guid: "drun-h2",
    startedAt: "2026-09-27T12:30:00Z",
    status: "timed_out",
    temporalWorkflowId: "WorkflowDefinitionRunWorkflow-81",
    endedAt: "2026-09-27T13:00:00Z",
  }),
  definitionRun({
    guid: "drun-h3",
    startedAt: "2026-09-27T13:30:00Z",
    status: "cancelled",
    temporalWorkflowId: "WorkflowDefinitionRunWorkflow-82",
    currentStageRole: "",
    endedAt: "2026-09-27T14:00:00Z",
  }),
];

export const LONG_RUN = definitionRun({
  guid: "drun-long",
  definitionName: LONG,
  definitionSlug: LONG,
  projectSlug: LONG,
  temporalWorkflowId: `WorkflowDefinitionRunWorkflow-${LONG}`,
  currentStageRole: LONG,
});

// ─── /workflows screen ───────────────────────────────────────────────────────

/** Every row the list joins from the fixtures: configured workflows, definitions, templates. */
export const WORKFLOW_ROWS: WorkflowRow[] = joinWorkflows(
  CONFIGURED,
  DEFINITIONS,
  [...RUNNING_RUNS, ...HISTORICAL_RUNS],
  { runsComplete: true }
);

export const LONG_ROWS: WorkflowRow[] = joinWorkflows(
  [LONG_CONFIGURED],
  [LONG_DEFINITION],
  [LONG_RUN],
  { runsComplete: true }
);

/** Enough rows for three pages at 25. */
export const MANY_ROWS: WorkflowRow[] = Array.from({ length: 60 }, (_, i) => ({
  ...WORKFLOW_ROWS[i % 3],
  id: `many-${i}`,
  slug: `workflow-${String(i).padStart(2, "0")}`,
  name: `Workflow ${String(i).padStart(2, "0")}`,
}));

/** The screen's props without the list controller, which a story makes. */
export function listProps(
  overrides: Partial<Omit<WorkflowsListScreenProps, "list">> = {}
): Omit<WorkflowsListScreenProps, "list"> {
  return {
    rows: WORKFLOW_ROWS,
    totalCount: WORKFLOW_ROWS.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    truncatedAt: null,
    canCreate: true,
    canManage: true,
    canRun: true,
    canViewPlatformRuns: true,
    busySlug: null,
    onRun: asyncNoop,
    onToggle: asyncNoop,
    onDelete: asyncNoop,
    onClone: asyncNoop,
    ...overrides,
  };
}

// ─── Platform instances ─────────────────────────────────────────────────────────

const instance = (overrides: Partial<WorkflowInstance>): WorkflowInstance => ({
  workflowId: "deploy-app-billing-api-7f3c",
  workflowType: "DeployAppWorkflow",
  runId: "7f3c2a91-4b1e-4c8a-9d2f-0e5b6a7c8d9e",
  status: "RUNNING",
  startedAt: "2026-09-28T11:58:00Z",
  closedAt: "",
  durationSeconds: 42.3,
  taskQueue: "astrolift-deploys",
  triggeredBy: "leo",
  ...overrides,
});

export const INSTANCES: WorkflowInstance[] = [
  instance({}),
  instance({
    workflowId: "provision-cluster-prod-west-1a2b",
    workflowType: "ProvisionClusterWorkflow",
    runId: "1a2b3c4d-0000-4000-8000-000000000002",
    durationSeconds: 754,
    triggeredBy: "",
  }),
  instance({
    workflowId: "drift-detect-nightly-9e8d",
    workflowType: "DriftDetectionWorkflow",
    runId: "9e8d7c6b-0000-4000-8000-000000000003",
    status: "RUNNING",
    durationSeconds: 7260,
    startedAt: "2026-09-28T10:00:00Z",
  }),
];

export const LONG_INSTANCE = instance({
  workflowId: `deploy-app-${LONG}`,
  workflowType: `DeployAppWorkflow${LONG}`,
  triggeredBy: "platform-engineering-automation-service-account@astrolift.example",
});

export const INSTANCE_DETAIL: WorkflowInstanceDetail = {
  instance: INSTANCES[0],
  history: [
    {
      eventType: "WorkflowExecutionStarted",
      timestamp: "2026-09-28T11:58:00Z",
      payload: json({}),
      retryCount: 0,
      decision: "",
    },
    {
      eventType: "ActivityTaskCompleted",
      timestamp: "2026-09-28T11:58:10Z",
      payload: json({ activity: "build_image", image: "ghcr.io/example/billing-api:f1f9f11a" }),
      retryCount: 1,
      decision: "completed",
    },
    {
      eventType: "ActivityTaskFailed",
      timestamp: "2026-09-28T11:58:30Z",
      payload: json({ activity: "rollout", error: "deadline exceeded" }),
      retryCount: 3,
      decision: "failed",
    },
  ],
};

export function detailProps(
  overrides: Partial<InstanceDetailViewProps> = {}
): InstanceDetailViewProps {
  return {
    workflowId: INSTANCES[0].workflowId,
    detail: INSTANCE_DETAIL,
    loading: false,
    error: null,
    isAdmin: true,
    onClose: noop,
    adminControls: null,
    ...overrides,
  };
}

export function adminProps(
  overrides: Partial<InstanceAdminControlsViewProps> = {}
): InstanceAdminControlsViewProps {
  return {
    onCancel: asyncNoop,
    onTerminate: asyncNoop,
    cancelLoading: false,
    terminateLoading: false,
    ...overrides,
  };
}
