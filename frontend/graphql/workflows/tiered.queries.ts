import { gql } from "@apollo/client";

// Tiered workflows domain (spec 40). Operation names carry a "Tiered" /
// "Configured" qualifier where a legacy operation (workflows.queries.ts)
// already claimed the natural name — the legacy docs stay untouched because
// the Temporal instances panel still consumes them.

const CONFIGURED_WORKFLOW_FIELDS = `
  guid
  name
  slug
  description
  triggerKind
  scheduleCron
  isEnabled
  inputs
  stageBindings
  organizationGuid
  definitionSlug
  definitionName
  patternKind
  runCount
  createdAt
`;

const WORKFLOW_RUN_FIELDS = `
  guid
  currentState
  temporalWorkflowId
  temporalRunId
  startedAt
  completedAt
  isCompleted
`;

const DEFINITION_SUMMARY_FIELDS = `
  guid
  name
  slug
  description
  patternKind
  isEnabled
  isGlobal
  organizationGuid
  projectGuid
  projectSlug
  projectTeamSlug
  sourceRepo
  sourcePath
  sourceRef
  stageCount
  stages {
    guid
    order
    kind
    role
    agentRef
    workflowRef
    agentGuid
    agentName
    agentSlug
    environmentSpecSlug
    resolvedModel
    hasPrompt
    outputKey
    skillRefs
    fanOutCount
    fanOutDynamic
    onFailure
    maxAttempts
    backEdge
    iteration
    timeoutSeconds
  }
  createdAt
`;

const STAGE_FIELDS = `
  guid
  order
  kind
  role
  prompt
  approvers
  agentRef
  workflowRef
  environmentSpecSlug
  outputKey
  skillRefs
  fanOutCount
  onFailure
  maxAttempts
  backEdge
  iteration
  timeoutSeconds
  createdAt
  agentDefinitionGuid
  agentDefinitionName
`;

// ─── Tier 2 — configured workflows ───────────────────────────────────────

export const LIST_CONFIGURED_WORKFLOWS = gql`
  query ListConfiguredWorkflows($orgId: ID) {
    workflows(orgId: $orgId) {
      ${CONFIGURED_WORKFLOW_FIELDS}
    }
  }
`;

export const GET_CONFIGURED_WORKFLOW = gql`
  query GetConfiguredWorkflow($slug: String!, $orgId: ID) {
    workflow(slug: $slug, orgId: $orgId) {
      ${CONFIGURED_WORKFLOW_FIELDS}
      runs {
        ${WORKFLOW_RUN_FIELDS}
      }
    }
  }
`;

// ─── Tier 1 — definitions + stages ───────────────────────────────────────

export const LIST_TIERED_WORKFLOW_DEFINITIONS = gql`
  query ListTieredWorkflowDefinitions($orgId: ID, $projectId: ID) {
    workflowDefinitions(orgId: $orgId, projectId: $projectId) {
      ${DEFINITION_SUMMARY_FIELDS}
    }
  }
`;

export const GET_TIERED_WORKFLOW_DEFINITION = gql`
  query GetTieredWorkflowDefinition($slug: String!, $orgId: ID) {
    workflowDefinition(slug: $slug, orgId: $orgId) {
      ${DEFINITION_SUMMARY_FIELDS}
    }
  }
`;

export const LIST_WORKFLOW_STAGES = gql`
  query ListWorkflowStages($workflowSlug: String!) {
    workflowStages(workflowSlug: $workflowSlug) {
      ${STAGE_FIELDS}
    }
  }
`;

// ─── Tier 3 — runs ───────────────────────────────────────────────────────

export const LIST_WORKFLOW_RUNS = gql`
  query ListTieredWorkflowRuns($workflowId: ID!, $orgId: ID) {
    workflowRuns(workflowId: $workflowId, orgId: $orgId) {
      ${WORKFLOW_RUN_FIELDS}
    }
  }
`;

export const LIST_WORKFLOW_DEFINITION_RUNS = gql`
  query ListWorkflowDefinitionRuns($orgId: ID, $projectId: ID, $status: String, $limit: Int) {
    workflowDefinitionRuns(orgId: $orgId, projectId: $projectId, status: $status, limit: $limit) {
      guid
      definitionGuid
      definitionSlug
      definitionName
      projectGuid
      projectSlug
      status
      temporalWorkflowId
      temporalRunId
      currentStageOrder
      currentStageRole
      parentRunGuid
      parentStageExecutionGuid
      nestingDepth
      childRunCount
      startedAt
      endedAt
    }
  }
`;

// One workflow definition run by guid (#2155), with the list's org and run
// visibility; null for a run the caller cannot see. The run page reads this
// instead of looking the run up in a capped window of the definition's runs.
export const GET_WORKFLOW_DEFINITION_RUN = gql`
  query GetWorkflowDefinitionRun($guid: String!, $orgId: ID) {
    workflowDefinitionRun(guid: $guid, orgId: $orgId) {
      guid
      definitionGuid
      definitionSlug
      definitionName
      projectGuid
      projectSlug
      status
      temporalWorkflowId
      temporalRunId
      currentStageOrder
      currentStageRole
      parentRunGuid
      parentStageExecutionGuid
      nestingDepth
      childRunCount
      startedAt
      endedAt
    }
  }
`;

export const LIST_PENDING_HUMAN_GATES = gql`
  query ListPendingHumanGates($orgId: ID, $limit: Int) {
    pendingHumanGates(orgId: $orgId, limit: $limit) {
      executionId
      runGuid
      workflowId
      definitionSlug
      definitionName
      stageRole
      stageApprovers
      startedAt
    }
  }
`;

export const LIST_WORKFLOW_STAGE_EXECUTIONS = gql`
  query ListWorkflowStageExecutions($workflowId: String!, $runId: String!) {
    workflowStageExecutions(workflowId: $workflowId, runId: $runId) {
      guid
      status
      attemptNumber
      roundNumber
      causedBy {
        edge
        reason
        maxRounds
        edgeRound
      }
      fanoutStageId
      fanoutParentExecutionGuid
      fanoutIndex
      collectionIndex
      collectionStageId
      collectionParentExecutionGuid
      startedAt
      endedAt
      output
      failure
      errorMessage
      createdAt
      executionId
      stageGuid
      stageKind
      stageOrder
      agentRunGuid
      childWorkflowRunGuid
      childWorkflowDefinitionSlug
      childWorkflowStatus
      stageRole
      stageApprovers
      humanGateState
    }
  }
`;

// ─── Manifest (TOML code view) ───────────────────────────────────────────

export const EXPORT_WORKFLOW_MANIFEST = gql`
  query ExportWorkflowManifest($definitionSlug: String!) {
    exportWorkflowManifest(definitionSlug: $definitionSlug) {
      ok
      toml
      error
    }
  }
`;

export const PREVIEW_WORKFLOW_MANIFEST = gql`
  query PreviewWorkflowManifest($toml: String!) {
    previewWorkflowManifest(toml: $toml) {
      ok
      error
      errorPath
      errorLine
      errorColumn
      definition {
        slug
        name
        pattern
        description
      }
      stages {
        order
        kind
        role
        agent
        workflow
        environmentSpecSlug
        skills
        onFailure
        maxAttempts
        backEdge
        iteration
        timeout
        fanOut
        prompt
        outputKey
        approvers
      }
    }
  }
`;
