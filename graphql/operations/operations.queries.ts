import { gql } from "@apollo/client";

export const LIST_EVENTS = gql`
  query ListEvents($limit: Int, $eventType: String) {
    astroliftEvents(limit: $limit, eventType: $eventType) {
      id
      eventType
      payload
      organizationId
      teamId
      projectId
      registeredAppId
      occurredAt
    }
  }
`;

export const LIST_AUDIT_EVENTS = gql`
  query ListAuditEvents($limit: Int, $action: String, $decision: String) {
    astroliftAuditEvents(limit: $limit, action: $action, decision: $decision) {
      id
      organizationId
      occurredAt
      actorKind
      actorId
      actorDisplay
      action
      decision
      targetKind
      targetId
      targetSlug
      requestId
      data
    }
  }
`;

export const LIST_WORKFLOW_RUNS = gql`
  query ListWorkflowRuns($limit: Int) {
    astroliftWorkflowRuns(limit: $limit) {
      id
      workflowKind
      workflowId
      runId
      status
      startedAt
      endedAt
      organizationId
      registeredAppId
      failure
    }
  }
`;
