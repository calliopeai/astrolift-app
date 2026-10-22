import { gql } from "@apollo/client";

export const ACTIVATE_REVIEWED_WORKFLOW_SCHEDULE = gql`
  mutation ActivateReviewedWorkflowSchedule($input: ActivateReviewedWorkflowScheduleInput!) {
    activateReviewedWorkflowSchedule(input: $input) {
      ok
      errors {
        code
        message
        field
        requiresAttestation
        supportedMethods
      }
      data {
        id
        workflowId
        definitionId
        approvedByUserId
        approvedAt
        reviewDigest
        definitionRevision
        configurationDigest
        compiledPlansDigest
        scheduleReviewDigest
        credentialKind
        credentialExpiresAt
        isCurrent
        databaseStatus
        engineState
      }
    }
  }
`;
