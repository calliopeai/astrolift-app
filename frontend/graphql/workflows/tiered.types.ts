// Tiered workflows domain (spec 40) — configured Workflows (tier 2) over
// WorkflowDefinitions (tier 1) with WorkflowRuns (tier 3). Hand-typed against
// schema.graphql; the legacy state-machine + Temporal-instance shapes stay in
// workflows.types.ts (the instances panel still consumes them).

// ─── Shared envelopes ────────────────────────────────────────────────────

export type TieredValidationError = {
  field: string;
  messages: string[];
};

// Matches the schema's `MutationResult` (ok + [ValidationError!]!) as used by
// the tiered workflow mutations (updateWorkflowStage, deleteWorkflowStage,
// reorderWorkflowStages, updateWorkflowDefinition, deleteWorkflowDefinition,
// deleteWorkflow).
export type TieredMutationResult = {
  ok: boolean;
  errors: TieredValidationError[];
};

// ─── Tier 1 — definitions + stages ───────────────────────────────────────

// `workflowDefinitions` / `workflowDefinition` row (WorkflowDefinitionSummary).
export type WorkflowDefinitionSummary = {
  guid: string;
  name: string;
  slug: string;
  description: string;
  patternKind: string;
  isEnabled: boolean;
  isGlobal: boolean;
  organizationGuid: string | null;
  projectGuid: string | null;
  projectSlug: string;
  projectTeamSlug: string;
  sourceRepo: string;
  sourcePath: string;
  sourceRef: string;
  stageCount: number;
  stages: WorkflowTopologyStage[];
  createdAt: string;
};

export type WorkflowTopologyStage = {
  guid: string;
  order: number;
  kind: string;
  role: string;
  agentRef: string;
  agentGuid: string | null;
  agentName: string;
  agentSlug: string;
  environmentSpecSlug: string;
  resolvedModel: string;
  hasPrompt: boolean;
  outputKey: string;
  skillRefs: string[];
  fanOutCount: number | null;
  fanOutDynamic: boolean;
  onFailure: string;
  timeoutSeconds: number;
};

// `workflowStages(workflowSlug)` row (WorkflowStageType).
export type WorkflowStage = {
  guid: string;
  order: number;
  kind: string;
  role: string;
  prompt: string;
  approvers: unknown;
  agentRef: string;
  environmentSpecSlug: string;
  outputKey: string;
  skillRefs: unknown;
  fanOutCount: number | null;
  onFailure: string;
  timeoutSeconds: number;
  createdAt: string;
  agentDefinitionGuid: string | null;
  agentDefinitionName: string | null;
};

// ─── Tier 2 — configured workflows ───────────────────────────────────────

export type TieredWorkflowRun = {
  guid: string;
  currentState: string;
  temporalWorkflowId: string | null;
  temporalRunId: string | null;
  startedAt: string;
  completedAt: string | null;
  isCompleted: boolean;
};

export type WorkflowDefinitionRun = {
  guid: string;
  definitionGuid: string;
  definitionSlug: string;
  definitionName: string;
  projectGuid: string | null;
  projectSlug: string;
  status: string;
  temporalWorkflowId: string;
  temporalRunId: string | null;
  currentStageOrder: number | null;
  currentStageRole: string;
  startedAt: string | null;
  endedAt: string | null;
};

export type ConfiguredWorkflow = {
  guid: string;
  name: string;
  slug: string;
  description: string;
  triggerKind: string;
  scheduleCron: string | null;
  isEnabled: boolean;
  inputs: Record<string, unknown>;
  stageBindings: Record<string, unknown>;
  organizationGuid: string | null;
  definitionSlug: string;
  definitionName: string;
  patternKind: string;
  runCount: number;
  createdAt: string;
};

export type ConfiguredWorkflowWithRuns = ConfiguredWorkflow & {
  runs: TieredWorkflowRun[];
};

// ─── Tier 3 — run drill-down ─────────────────────────────────────────────

// `workflowStageExecutions(workflowId, runId)` row (WorkflowStageExecutionType).
export type WorkflowStageExecution = {
  guid: string;
  status: string;
  attemptNumber: number;
  startedAt: string | null;
  endedAt: string | null;
  output: unknown;
  failure: unknown;
  errorMessage: string;
  createdAt: string;
  executionId: string;
  stageGuid: string;
  stageKind: string;
  stageOrder: number;
  agentRunGuid: string | null;
};

// ─── Manifest (TOML code view) ───────────────────────────────────────────

export type WorkflowManifestDefinition = {
  slug: string;
  name: string;
  pattern: string;
  description: string;
};

export type WorkflowManifestStage = {
  order: number;
  kind: string;
  role: string;
  agent: string | null;
  environmentSpecSlug: string | null;
  skills: string[];
  onFailure: string;
  timeout: number;
  fanOut: string;
  prompt: string | null;
  outputKey: string | null;
  approvers: string[];
};

export type WorkflowManifestPreview = {
  ok: boolean;
  error: string | null;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
  definition: WorkflowManifestDefinition | null;
  stages: WorkflowManifestStage[];
};

export type WorkflowManifestExport = {
  ok: boolean;
  toml: string | null;
  error: string | null;
};

// ─── Query payloads ──────────────────────────────────────────────────────

export type TieredWorkflowsData = {
  workflows: ConfiguredWorkflow[];
};

export type TieredWorkflowData = {
  workflow: ConfiguredWorkflowWithRuns | null;
};

export type TieredWorkflowDefinitionsData = {
  workflowDefinitions: WorkflowDefinitionSummary[];
};

export type TieredWorkflowDefinitionsVars = {
  orgId?: string | null;
  projectId?: string | null;
};

export type TieredWorkflowDefinitionData = {
  workflowDefinition: WorkflowDefinitionSummary | null;
};

export type TieredWorkflowStagesData = {
  workflowStages: WorkflowStage[];
};

export type TieredWorkflowRunsData = {
  workflowRuns: TieredWorkflowRun[];
};

export type WorkflowDefinitionRunsData = {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
};

export type WorkflowDefinitionRunsVars = {
  orgId?: string | null;
  projectId?: string | null;
  status?: string | null;
  limit?: number | null;
};

export type TieredWorkflowStageExecutionsData = {
  workflowStageExecutions: WorkflowStageExecution[];
};

export type ExportWorkflowManifestData = {
  exportWorkflowManifest: WorkflowManifestExport;
};

export type PreviewWorkflowManifestData = {
  previewWorkflowManifest: WorkflowManifestPreview;
};

// ─── Mutation payloads ───────────────────────────────────────────────────

export type CreateConfiguredWorkflowData = {
  createWorkflow: TieredMutationResult & {
    workflow: ConfiguredWorkflow | null;
  };
};

export type UpdateConfiguredWorkflowData = {
  updateWorkflow: TieredMutationResult & {
    workflow: ConfiguredWorkflow | null;
  };
};

export type DeleteConfiguredWorkflowData = {
  deleteWorkflow: TieredMutationResult;
};

export type RunWorkflowData = {
  runWorkflow: TieredMutationResult & {
    runId: string | null;
    workflowRunId: string | null;
    instanceId: string | null;
  };
};

export type CloneWorkflowDefinitionData = {
  cloneWorkflowDefinition: TieredMutationResult & {
    slug: string | null;
    definition: {
      name: string;
      slug: string;
      description: string | null;
      patternKind: string;
      isEnabled: boolean;
      organizationGuid: string | null;
      createdAt: string;
    } | null;
  };
};

export type UpdateWorkflowDefinitionTieredData = {
  updateWorkflowDefinition: TieredMutationResult;
};

export type DeleteWorkflowDefinitionTieredData = {
  deleteWorkflowDefinition: TieredMutationResult;
};

export type RunWorkflowDefinitionData = {
  runWorkflowDefinition: TieredMutationResult & {
    workflowRunId: string | null;
    temporalWorkflowId: string | null;
  };
};

export type CreateWorkflowStageData = {
  createWorkflowStage: TieredMutationResult & {
    stage: WorkflowStage | null;
  };
};

export type UpdateWorkflowStageData = {
  updateWorkflowStage: TieredMutationResult;
};

export type DeleteWorkflowStageData = {
  deleteWorkflowStage: TieredMutationResult;
};

export type ReorderWorkflowStagesData = {
  reorderWorkflowStages: TieredMutationResult;
};

export type ImportWorkflowManifestData = {
  importWorkflowManifest: TieredMutationResult & {
    createdSlug: string | null;
    manifest: WorkflowManifestPreview | null;
  };
};
