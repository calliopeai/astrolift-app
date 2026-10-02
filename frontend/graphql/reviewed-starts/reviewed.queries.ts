import { gql } from "@apollo/client";

export const REVIEW_WORKFLOW_START = gql`
  query ReviewWorkflowStart($id: GUID!) {
    workflowDefinitionById(id: $id) {
      guid
      revision
      definition {
        name
        slug
        isEnabled
        stageCount
      }
      inputContract {
        schema
        digest
        supported
        error
        acceptsInputs
        supportsSimpleForm
        fields {
          name
          kind
          required
          hasDefault
          default
          sensitive
          simple
          enumValues
          constraints
        }
      }
    }
  }
`;
export const RECONCILE_WORKFLOW_START = gql`
  query ReconcileWorkflowStart($requestId: String!) {
    workflowDefinitionStartRequest(requestId: $requestId) {
      id
      requestId
      definitionId
      organizationId
      definitionRevision
      inputSchemaDigest
      executionId
      temporalWorkflowId
      temporalRunId
      dispatchStatus
      dispatchLastError
    }
  }
`;
export const REVIEW_PIPELINE_START = gql`
  query ReviewPipelineStart($id: String!) {
    astroliftPipeline(id: $id) {
      id
      name
      organizationId
      version
      defaultBranch
    }
  }
`;
export const RECONCILE_PIPELINE_START = gql`
  query ReconcilePipelineStart($pipelineId: GUID!, $requestId: String!) {
    pipelineStartRequest(pipelineId: $pipelineId, requestId: $requestId) {
      id
      requestId
      pipelineId
      organizationId
      pipelineVersion
      version
      temporalWorkflowId
      temporalRunId
      dispatchStatus
      dispatchLastError
      cancellationStatus
      cleanupStatus
    }
  }
`;
export const REVIEW_PIPELINE_CANCELLATION = gql`
  query ReviewPipelineCancellation($id: String!) {
    astroliftPipelineRun(id: $id) {
      id
      version
      status
      temporalWorkflowId
      temporalRunId
      cancellationStatus
      cleanupStatus
    }
  }
`;
export const EXACT_WORKFLOW_DEFINITION_FRAME = gql`
  query ExactWorkflowDefinitionFrame($id: GUID!) {
    workflowDefinitionById(id: $id) {
      definition {
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
        createdAt
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
          timeoutSeconds
        }
      }
    }
  }
`;
