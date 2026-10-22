import { gql } from "@apollo/client";

export const GET_WORKFLOW_DEFINITION_FULL_REVIEW = gql`
  query GetWorkflowDefinitionFullReview($id: GUID!) {
    workflowDefinitionFullReview(id: $id) {
      definitionId
      revision
      capturedAt
      consistency
      canonicalization
      reviewDigest
      canonicalReview
      rootManifest
      inputSchemaDigest
    }
  }
`;

export const GET_WORKFLOW_SCHEDULE_FULL_REVIEW = gql`
  query GetWorkflowScheduleFullReview($workflowId: GUID!) {
    workflowScheduleFullReview(workflowId: $workflowId) {
      definitionId
      workflowId
      revision
      capturedAt
      consistency
      canonicalization
      reviewDigest
      canonicalReview
      rootManifest
      inputSchemaDigest
      canonicalConfiguration
      configurationDigest
      compiledPlansDigest
      scheduleReviewDigest
      activationIntent
    }
  }
`;
