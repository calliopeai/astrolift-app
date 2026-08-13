import { gql } from "@apollo/client";

// Tiered workflows domain (spec 40). Legacy state-machine mutations stay in
// workflows.mutations.ts; operation names here carry a qualifier where the
// legacy layer already claimed the natural one.

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

const VALIDATION_ERROR_FIELDS = `
  errors {
    field
    messages
  }
`;

// ─── Tier 2 — configured workflows ───────────────────────────────────────

export const CREATE_CONFIGURED_WORKFLOW = gql`
  mutation CreateConfiguredWorkflow(
    $name: String!
    $definitionSlug: String!
    $slug: String
    $description: String
    $stageBindings: JSON
    $inputs: JSON
    $triggerKind: String! = "manual"
    $scheduleCron: String
    $orgId: ID
  ) {
    createWorkflow(
      name: $name
      definitionSlug: $definitionSlug
      slug: $slug
      description: $description
      stageBindings: $stageBindings
      inputs: $inputs
      triggerKind: $triggerKind
      scheduleCron: $scheduleCron
      orgId: $orgId
    ) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      workflow {
        ${CONFIGURED_WORKFLOW_FIELDS}
      }
    }
  }
`;

export const UPDATE_CONFIGURED_WORKFLOW = gql`
  mutation UpdateConfiguredWorkflow(
    $slug: String!
    $name: String
    $description: String
    $stageBindings: JSON
    $inputs: JSON
    $triggerKind: String
    $scheduleCron: String
    $isEnabled: Boolean
    $orgId: ID
  ) {
    updateWorkflow(
      slug: $slug
      name: $name
      description: $description
      stageBindings: $stageBindings
      inputs: $inputs
      triggerKind: $triggerKind
      scheduleCron: $scheduleCron
      isEnabled: $isEnabled
      orgId: $orgId
    ) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      workflow {
        ${CONFIGURED_WORKFLOW_FIELDS}
      }
    }
  }
`;

export const DELETE_CONFIGURED_WORKFLOW = gql`
  mutation DeleteConfiguredWorkflow($slug: String!, $orgId: ID) {
    deleteWorkflow(slug: $slug, orgId: $orgId) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

export const RUN_WORKFLOW = gql`
  mutation RunWorkflow($workflowId: ID!, $inputs: JSON, $orgId: ID) {
    runWorkflow(workflowId: $workflowId, inputs: $inputs, orgId: $orgId) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      runId
      workflowRunId
      instanceId
    }
  }
`;

// ─── Tier 1 — definitions ────────────────────────────────────────────────

export const CLONE_WORKFLOW_DEFINITION = gql`
  mutation CloneWorkflowDefinition($slug: String!, $orgId: ID) {
    cloneWorkflowDefinition(slug: $slug, orgId: $orgId) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      slug
      definition {
        name
        slug
        description
        patternKind
        isEnabled
        organizationGuid
        createdAt
      }
    }
  }
`;

export const UPDATE_WORKFLOW_DEFINITION_TIERED = gql`
  mutation UpdateWorkflowDefinitionTiered(
    $slug: String!
    $name: String
    $description: String
    $patternKind: String
    $isEnabled: Boolean
  ) {
    updateWorkflowDefinition(
      slug: $slug
      name: $name
      description: $description
      patternKind: $patternKind
      isEnabled: $isEnabled
    ) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

export const DELETE_WORKFLOW_DEFINITION_TIERED = gql`
  mutation DeleteWorkflowDefinitionTiered($slug: String!) {
    deleteWorkflowDefinition(slug: $slug) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

export const RUN_WORKFLOW_DEFINITION = gql`
  mutation RunWorkflowDefinition($workflowSlug: String!, $triggerPayload: JSON) {
    runWorkflowDefinition(workflowSlug: $workflowSlug, triggerPayload: $triggerPayload) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      workflowRunId
      temporalWorkflowId
    }
  }
`;

// ─── Stages ──────────────────────────────────────────────────────────────

export const CREATE_WORKFLOW_STAGE = gql`
  mutation CreateWorkflowStage(
    $workflowSlug: String!
    $kind: String!
    $order: Int
    $role: String
    $onFailure: String! = "fail"
    $timeoutSeconds: Int! = 300
    $agentDefinitionGuid: String
    $agentRef: String
    $environmentSpecSlug: String
    $skillRefs: JSON
    $fanOutCount: Int
    $prompt: String
    $outputKey: String
    $approvers: JSON
  ) {
    createWorkflowStage(
      workflowSlug: $workflowSlug
      kind: $kind
      order: $order
      role: $role
      onFailure: $onFailure
      timeoutSeconds: $timeoutSeconds
      agentDefinitionGuid: $agentDefinitionGuid
      agentRef: $agentRef
      environmentSpecSlug: $environmentSpecSlug
      skillRefs: $skillRefs
      fanOutCount: $fanOutCount
      prompt: $prompt
      outputKey: $outputKey
      approvers: $approvers
    ) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      stage {
        guid
        order
        kind
        role
        prompt
        approvers
        agentRef
        environmentSpecSlug
        outputKey
        skillRefs
        fanOutCount
        onFailure
        timeoutSeconds
        createdAt
        agentDefinitionGuid
        agentDefinitionName
      }
    }
  }
`;

export const UPDATE_WORKFLOW_STAGE = gql`
  mutation UpdateWorkflowStage(
    $stageGuid: ID!
    $kind: String
    $role: String
    $onFailure: String
    $timeoutSeconds: Int
    $agentDefinitionGuid: String
    $agentRef: String
    $environmentSpecSlug: String
    $skillRefs: JSON
    $fanOutCount: Int
    $prompt: String
    $outputKey: String
    $approvers: JSON
  ) {
    updateWorkflowStage(
      stageGuid: $stageGuid
      kind: $kind
      role: $role
      onFailure: $onFailure
      timeoutSeconds: $timeoutSeconds
      agentDefinitionGuid: $agentDefinitionGuid
      agentRef: $agentRef
      environmentSpecSlug: $environmentSpecSlug
      skillRefs: $skillRefs
      fanOutCount: $fanOutCount
      prompt: $prompt
      outputKey: $outputKey
      approvers: $approvers
    ) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

export const DELETE_WORKFLOW_STAGE = gql`
  mutation DeleteWorkflowStage($stageGuid: ID!) {
    deleteWorkflowStage(stageGuid: $stageGuid) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

export const REORDER_WORKFLOW_STAGES = gql`
  mutation ReorderWorkflowStages($definitionSlug: String!, $stageGuids: [ID!]!) {
    reorderWorkflowStages(definitionSlug: $definitionSlug, stageGuids: $stageGuids) {
      ok
      ${VALIDATION_ERROR_FIELDS}
    }
  }
`;

// ─── Manifest import ─────────────────────────────────────────────────────

export const IMPORT_WORKFLOW_MANIFEST = gql`
  mutation ImportWorkflowManifest($toml: String!, $preview: Boolean! = true, $orgId: ID) {
    importWorkflowManifest(toml: $toml, preview: $preview, orgId: $orgId) {
      ok
      ${VALIDATION_ERROR_FIELDS}
      createdSlug
      manifest {
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
          environmentSpecSlug
          skills
          onFailure
          timeout
          fanOut
          prompt
          outputKey
          approvers
        }
      }
    }
  }
`;
