import { gql } from "@apollo/client";
export const TELEMETRY_APPS = gql`
  query TelemetryApps($search: String, $cursor: String, $limit: Int!) {
    astroliftAppsPage(search: $search, cursor: $cursor, limit: $limit, includeArchived: false) {
      items {
        id
        slug
        name
      }
      nextCursor
      totalCount
    }
  }
`;
export const TELEMETRY_ENVIRONMENTS = gql`
  query TelemetryEnvironments($appSlug: String!, $page: Int!, $pageSize: Int!) {
    astroliftEnvironmentsPage(appSlug: $appSlug, page: $page, pageSize: $pageSize) {
      items {
        id
        name
        kind
        clusterId
      }
      totalCount
      page
      pageSize
    }
  }
`;
export const EXPLORER_LOGS = gql`
  query ExplorerLogs(
    $appSlug: String!
    $environmentName: String!
    $environmentId: GUID!
    $since: DateTime!
    $until: DateTime!
    $search: String
    $level: String
    $cursor: String
    $limit: Int!
  ) {
    astroliftAppLogs(
      appSlug: $appSlug
      environmentName: $environmentName
      environmentId: $environmentId
      since: $since
      until: $until
      search: $search
      level: $level
      cursor: $cursor
      limit: $limit
    ) {
      reason
      historicalAvailable
      nextCursor
      reachedRetention
      totalCount
      scope {
        organizationId
        appId
        environmentId
        environmentName
        clusterId
        namespace
      }
      items {
        podName
        container
        timestamp
        message
        level
        stream
      }
    }
  }
`;
export const EXPLORER_TRACES = gql`
  query ExplorerTraces(
    $appSlug: String!
    $environmentName: String!
    $environmentId: GUID!
    $since: String!
    $until: String!
    $service: String
    $status: String
    $limit: Int!
  ) {
    astroliftAppTracePage(
      appSlug: $appSlug
      environmentName: $environmentName
      environmentId: $environmentId
      since: $since
      until: $until
      service: $service
      status: $status
      limit: $limit
    ) {
      reason
      truncated
      limit
      scope {
        organizationId
        appId
        environmentId
        environmentName
        clusterId
        namespace
      }
      items {
        traceId
        rootService
        rootOperation
        spanCount
        durationMs
        statusCode
      }
    }
  }
`;
export const EXPLORER_SPANS = gql`
  query ExplorerSpans(
    $appSlug: String!
    $environmentName: String!
    $environmentId: GUID!
    $traceId: String!
    $since: String!
    $until: String!
  ) {
    astroliftTraceSpansResult(
      appSlug: $appSlug
      environmentName: $environmentName
      environmentId: $environmentId
      traceId: $traceId
      since: $since
      until: $until
    ) {
      reason
      scope {
        organizationId
        appId
        environmentId
        environmentName
        clusterId
        namespace
      }
      items {
        traceId
        spanId
        parentSpanId
        operation
        service
        startTime
        durationMs
        statusCode
        attributes
        resourceAttributes
      }
    }
  }
`;
