import { gql } from "@apollo/client";

export const GET_RECENT_ACTIVITY = gql`
  query GetRecentActivity($limit: Int, $cursor: String) {
    astroliftRecentActivity(limit: $limit, cursor: $cursor) {
      items {
        id
        eventType
        action
        actorDisplay
        targetKind
        targetLabel
        targetHref
        occurredAt
        payload
      }
      nextCursor
    }
  }
`;

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
      resourceKind
      resourceId
    }
  }
`;

export const LIST_EVENTS_AGGREGATED = gql`
  query ListEventsAggregated(
    $limit: Int
    $eventType: String
    $aggregateWindowSeconds: Int
  ) {
    astroliftEventsAggregated(
      limit: $limit
      eventType: $eventType
      aggregateWindowSeconds: $aggregateWindowSeconds
    ) {
      representative {
        id
        eventType
        payload
        organizationId
        teamId
        projectId
        registeredAppId
        occurredAt
        resourceKind
        resourceId
      }
      count
      firstAt
      lastAt
      eventType
      resourceKind
      resourceId
    }
  }
`;

export const LIST_AUDIT_EVENTS = gql`
  query ListAuditEvents(
    $limit: Int
    $action: String
    $decision: String
    $actorId: String
    $createdAtGte: DateTime
    $createdAtLte: DateTime
  ) {
    astroliftAuditEvents(
      limit: $limit
      action: $action
      decision: $decision
      actorId: $actorId
      createdAtGte: $createdAtGte
      createdAtLte: $createdAtLte
    ) {
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
      before
      after
    }
  }
`;

export const LIST_AUDIT_EVENTS_PAGE = gql`
  query ListAuditEventsPage(
    $limit: Int
    $after: String
    $action: String
    $decision: String
    $actorId: String
    $createdAtGte: DateTime
    $createdAtLte: DateTime
    $includeTotal: Boolean
  ) {
    astroliftAuditEventsPage(
      limit: $limit
      after: $after
      action: $action
      decision: $decision
      actorId: $actorId
      createdAtGte: $createdAtGte
      createdAtLte: $createdAtLte
      includeTotal: $includeTotal
    ) {
      items {
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
        before
        after
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_AUDIT_RETENTION = gql`
  query GetAuditRetention {
    astroliftAuditRetention {
      days
    }
  }
`;

export const LIST_WEBHOOKS = gql`
  query ListWebhooks($appSlug: String) {
    astroliftWebhookSubscriptions(appSlug: $appSlug) {
      id
      url
      events
      isActive
      format
      lastDeliveryAt
      lastResponseStatus
      failureCount
      secretRotatedAt
      createdAt
      version
    }
  }
`;

export const LIST_WEBHOOK_DELIVERIES = gql`
  query ListWebhookDeliveries($subscriptionId: GUID!, $limit: Int) {
    astroliftWebhookDeliveries(subscriptionId: $subscriptionId, limit: $limit) {
      id
      subscriptionId
      eventType
      retryAttempt
      statusCode
      latencyMs
      success
      isTest
      requestPayloadExcerpt
      responseBodyExcerpt
      error
      deliveryId
      deliveredAt
    }
  }
`;

export const LIST_MY_NOTIFICATIONS = gql`
  query ListMyNotifications($unreadOnly: Boolean, $limit: Int) {
    astroliftMyNotifications(unreadOnly: $unreadOnly, limit: $limit) {
      id
      userId
      kind
      title
      body
      link
      readAt
      createdAt
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
