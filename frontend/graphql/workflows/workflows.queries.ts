import { gql } from "@apollo/client";

// ─── Temporal instance viewer (#437) ────────────────────────────────────

export const GET_WORKFLOW_INSTANCES = gql`
  query GetWorkflowInstances($workflowType: String, $status: String, $limit: Int!, $after: String) {
    astroliftWorkflowInstances(
      workflowType: $workflowType
      status: $status
      limit: $limit
      after: $after
    ) {
      items {
        workflowId
        workflowType
        runId
        status
        startedAt
        closedAt
        durationSeconds
        taskQueue
        triggeredBy
      }
      nextCursor
    }
  }
`;

export const GET_WORKFLOW_INSTANCE_DETAIL = gql`
  query GetWorkflowInstanceDetail($workflowId: String!, $runId: String) {
    astroliftWorkflowInstanceDetail(workflowId: $workflowId, runId: $runId) {
      instance {
        workflowId
        workflowType
        runId
        status
        startedAt
        closedAt
        durationSeconds
        taskQueue
        triggeredBy
      }
      history {
        eventType
        timestamp
        payload
        retryCount
        decision
      }
    }
  }
`;
