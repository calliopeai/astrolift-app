import { gql } from "@apollo/client";

export const START_REVIEWED_WORKFLOW = gql`
  mutation StartReviewedWorkflow($input: StartWorkflowDefinitionInput!) {
    startWorkflowDefinition(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
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
  }
`;
export const START_REVIEWED_PIPELINE = gql`
  mutation StartReviewedPipeline($input: StartPipelineRunInput!) {
    startPipelineRun(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
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
  }
`;
export const CANCEL_REVIEWED_PIPELINE = gql`
  mutation CancelReviewedPipeline(
    $runId: GUID!
    $expectedVersion: Int!
    $temporalWorkflowId: String!
    $temporalRunId: String!
  ) {
    cancelPipelineRun(
      runId: $runId
      expectedVersion: $expectedVersion
      temporalWorkflowId: $temporalWorkflowId
      temporalRunId: $temporalRunId
      confirmed: true
    ) {
      ok
      errors {
        code
        message
      }
      data {
        id
        version
        status
        temporalWorkflowId
        temporalRunId
        cancellationStatus
        cleanupStatus
        cancellationLastError
      }
    }
  }
`;
