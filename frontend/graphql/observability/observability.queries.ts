/**
 * App > Observability golden-signals queries (#380).
 *
 * Backed by the cluster-side Prometheus stack via the
 * ``astrolift_observability`` resolver. Both queries degrade to an
 * empty list / null when Prometheus isn't reachable or the cluster has
 * no ``prometheus_endpoint`` configured — the UI surfaces that as
 * "metrics not yet flowing".
 */

import { gql } from "@apollo/client";

const TIME_SERIES_POINT_FIELDS = `
  ts
  value
`;

export const GET_APP_GOLDEN_SIGNALS = gql`
  query GetAppGoldenSignals(
    $appSlug: String!
    $environmentName: String
    $workloadSlug: String
    $rangeSeconds: Int
  ) {
    astroliftAppGoldenSignals(
      appSlug: $appSlug
      environmentName: $environmentName
      workloadSlug: $workloadSlug
      rangeSeconds: $rangeSeconds
    ) {
      name
      rangeSeconds
      unit
      promql
      samples {
        ${TIME_SERIES_POINT_FIELDS}
      }
    }
  }
`;

export const GET_APP_STATUS_CODE_BREAKDOWN = gql`
  query GetAppStatusCodeBreakdown(
    $appSlug: String!
    $environmentName: String
    $workloadSlug: String
    $rangeSeconds: Int
  ) {
    astroliftAppStatusCodeBreakdown(
      appSlug: $appSlug
      environmentName: $environmentName
      workloadSlug: $workloadSlug
      rangeSeconds: $rangeSeconds
    ) {
      rangeSeconds
      promql
      series {
        codeClass
        topCodes
        samples {
          ${TIME_SERIES_POINT_FIELDS}
        }
      }
    }
  }
`;

// Live HTTP health probe (#406). Backend issues a sync GET, classifies
// (ok/degraded/down) by status code + latency, and caches the result
// per (app, url) for 30s. `forceRefresh: true` skips the cache for the
// click-to-recheck affordance on the pill.
const APP_URL_HEALTH_FIELDS = `
  url
  status
  statusCode
  latencyMs
  lastChecked
  message
`;

export const GET_APP_URL_HEALTH = gql`
  query GetAppUrlHealth($appSlug: String!, $url: String!, $forceRefresh: Boolean! = false) {
    astroliftAppUrlHealth(appSlug: $appSlug, url: $url, forceRefresh: $forceRefresh) {
      ${APP_URL_HEALTH_FIELDS}
    }
  }
`;

export const GET_APP_URL_PROBE_HISTORY = gql`
  query GetAppUrlProbeHistory($appSlug: String!, $url: String!, $limit: Int! = 5) {
    astroliftAppUrlProbeHistory(appSlug: $appSlug, url: $url, limit: $limit) {
      ${APP_URL_HEALTH_FIELDS}
    }
  }
`;

/**
 * Live resource usage gauges for one workload (#430).
 *
 * Returns null when the cluster has no Prometheus endpoint or every
 * underlying PromQL call errored — the workload-detail page renders
 * the "metrics not flowing" callout in either case.
 */
export const GET_WORKLOAD_RESOURCE_USAGE = gql`
  query GetWorkloadResourceUsage(
    $appSlug: String!
    $workloadSlug: String!
    $environmentName: String
  ) {
    astroliftWorkloadResourceUsage(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      environmentName: $environmentName
    ) {
      sourcedAt
      cpu {
        unit
        current
        request
        limit
        percentOfRequest
        percentOfLimit
      }
      memory {
        unit
        current
        request
        limit
        percentOfRequest
        percentOfLimit
      }
    }
  }
`;

/**
 * Historical (time-range) log query (#482).
 *
 * Used when the operator picks a time range instead of "Live tail".
 * Hits the cluster's wired log-aggregator backend (Loki / CloudWatch
 * / Stackdriver / Azure Monitor). Falls back to an empty page with
 * `historicalAvailable: false` when the cluster has no aggregator
 * configured — the LogViewer renders the "live tail only" badge in
 * that case.
 *
 * `cursor` is opaque to the FE; pass the previous page's
 * `nextCursor` back unchanged to fetch the next slice. Empty
 * `nextCursor` means end-of-window.
 */
export const GET_APP_LOGS = gql`
  query GetAppLogs(
    $appSlug: String!
    $since: DateTime!
    $until: DateTime!
    $environmentName: String
    $workloadSlug: String
    $level: String
    $search: String
    $limit: Int! = 500
    $cursor: String
  ) {
    astroliftAppLogs(
      appSlug: $appSlug
      since: $since
      until: $until
      environmentName: $environmentName
      workloadSlug: $workloadSlug
      level: $level
      search: $search
      limit: $limit
      cursor: $cursor
    ) {
      nextCursor
      reachedRetention
      historicalAvailable
      totalCount
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
