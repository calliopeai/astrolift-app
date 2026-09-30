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
  query ListEvents($limit: Int, $eventType: String, $appSlug: String) {
    astroliftEvents(limit: $limit, eventType: $eventType, appSlug: $appSlug) {
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

/**
 * Cursor-paginated companion to ``LIST_EVENTS`` (#1230).
 *
 * Two things about this page envelope are unlike every other one in the
 * schema, and a table over it has to account for both:
 *
 *  - ``AstroliftEventPage`` has NO ``totalCount``. The event stream is
 *    unbounded and counting it is a table scan, so the field was never
 *    added. ``useCursorTable`` already treats a missing count as null and
 *    DataTable renders the range without a total, so nothing needs to
 *    special-case it — but do not add ``totalCount`` to this selection
 *    expecting it to resolve.
 *  - It carries ``reason`` (``AstroliftObservabilityPanelReason``), the
 *    observability panels' shared "why is this empty" signal —
 *    ``NOT_CONFIGURED`` / ``NOT_SUPPORTED_BY_PROVIDER`` / ``NO_DATA_YET``
 *    tell a genuinely empty stream apart from one that cannot report.
 *    Selected here so the surface can say which, rather than showing the
 *    same "no events" copy for all three.
 *
 * ``severity`` is selected as well as filtered on: the raw list query
 * predates the field, and a table that can filter by severity has to be
 * able to show it.
 */
const EVENT_FIELDS = gql`
  fragment EventFields on AstroliftEvent {
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
    severity
  }
`;

export const LIST_EVENTS_PAGE = gql`
  ${EVENT_FIELDS}
  query ListEventsPage(
    $limit: Int
    $after: String
    $eventType: String
    $severity: String
    $appSlug: String
    $search: String
  ) {
    astroliftEventsPage(
      limit: $limit
      after: $after
      eventType: $eventType
      severity: $severity
      appSlug: $appSlug
      search: $search
    ) {
      items {
        ...EventFields
      }
      nextCursor
      reason
    }
  }
`;

export const LIST_EVENTS_AGGREGATED = gql`
  query ListEventsAggregated($limit: Int, $eventType: String, $aggregateWindowSeconds: Int) {
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

/**
 * Cursor-paginated companion to ``LIST_EVENTS_AGGREGATED`` (#1230).
 *
 * Unlike the raw event page this one DOES carry ``totalCount`` — it counts
 * buckets, not events. ``aggregateWindowSeconds`` is the roll-up window
 * (schema default 300); pass it as a static controller variable, since
 * changing it changes the question and has to restart the walk at page one.
 */
export const LIST_EVENTS_AGGREGATED_PAGE = gql`
  ${EVENT_FIELDS}
  query ListEventsAggregatedPage(
    $limit: Int
    $after: String
    $eventType: String
    $severity: String
    $appSlug: String
    $search: String
    $aggregateWindowSeconds: Int
  ) {
    astroliftEventsAggregatedPage(
      limit: $limit
      after: $after
      eventType: $eventType
      severity: $severity
      appSlug: $appSlug
      search: $search
      aggregateWindowSeconds: $aggregateWindowSeconds
    ) {
      items {
        representative {
          ...EventFields
        }
        count
        firstAt
        lastAt
        eventType
        resourceKind
        resourceId
      }
      nextCursor
      totalCount
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

/**
 * The audit trail (#433) on the list contract (#2151): ``search`` matches an
 * action prefix, the actor, the target slug or id and the request id;
 * ``filter`` carries the list's chips (actor and ``subjectUser`` take "me",
 * ``targetKind`` is case-insensitive). The single-value arguments stay for
 * the callers that still send them; every argument ANDs with the rest.
 */
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
    $search: String
    $filter: AstroliftAuditEventsFilter
    $sort: String
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
      search: $search
      filter: $filter
      sort: $sort
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

/**
 * Cursor-paginated companion to ``LIST_WEBHOOKS`` (#1230). ``appSlug``
 * stays optional — omitted, the field pages every subscription in the org
 * (the platform-wide Webhooks surface); passed, it scopes to one app's
 * settings tab. No sort argument, so no ``sortVariable`` / ``Column.sortKey``.
 */
const WEBHOOK_SUBSCRIPTION_FIELDS = gql`
  fragment WebhookSubscriptionFields on AstroliftWebhookSubscription {
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
`;

export const LIST_WEBHOOKS_PAGE = gql`
  ${WEBHOOK_SUBSCRIPTION_FIELDS}
  query ListWebhooksPage($appSlug: String, $search: String, $limit: Int, $after: String) {
    astroliftWebhookSubscriptionsPage(
      appSlug: $appSlug
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ...WebhookSubscriptionFields
      }
      nextCursor
      totalCount
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

/**
 * Cursor-paginated companion to ``LIST_WEBHOOK_DELIVERIES`` (#1230).
 * ``subscriptionId`` is required — a delivery only exists under one
 * subscription — so the delivery table stays skipped until a subscription
 * is selected.
 */
const WEBHOOK_DELIVERY_FIELDS = gql`
  fragment WebhookDeliveryFields on AstroliftWebhookDelivery {
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
`;

export const LIST_WEBHOOK_DELIVERIES_PAGE = gql`
  ${WEBHOOK_DELIVERY_FIELDS}
  query ListWebhookDeliveriesPage(
    $subscriptionId: GUID!
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftWebhookDeliveriesPage(
      subscriptionId: $subscriptionId
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ...WebhookDeliveryFields
      }
      nextCursor
      totalCount
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

export const GET_EVENT = gql`
  ${EVENT_FIELDS}
  query GetEvent($id: GUID!) {
    astroliftEvent(id: $id) {
      ...EventFields
    }
  }
`;

export const GET_APP_SECURITY_EVENTS = gql`
  ${EVENT_FIELDS}
  query GetAppSecurityEvents($appSlug: String!) {
    signing: astroliftEvents(appSlug: $appSlug, eventType: "image.signed", limit: 1) {
      ...EventFields
    }
    scan: astroliftEvents(appSlug: $appSlug, eventType: "image.scanned", limit: 1) {
      ...EventFields
    }
    sbom: astroliftEvents(appSlug: $appSlug, eventType: "sbom.generated", limit: 1) {
      ...EventFields
    }
  }
`;
