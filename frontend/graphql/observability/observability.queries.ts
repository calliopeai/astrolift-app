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
      reason
      signals {
        name
        rangeSeconds
        unit
        promql
        reason
        samples {
          ${TIME_SERIES_POINT_FIELDS}
        }
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
      reason
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

/**
 * Per-managed-service metric envelope (#645 + #646).
 *
 * RDS-style tile when the app has a `postgres` managed service;
 * S3-style tile when it has an `object_store` managed service.
 * Returns null for unsupported kinds (anything else today); the panel
 * skips render in that case.
 *
 * The resolver narrows to one ManagedService row by guid so a single
 * panel renders per binding. Callers iterate the app's managed-service
 * list and render one panel per supported binding.
 */
export const GET_MANAGED_SERVICE_METRICS = gql`
  query GetManagedServiceMetrics($managedServiceId: ID!, $rangeSeconds: Int) {
    astroliftAppManagedServiceMetrics(
      managedServiceId: $managedServiceId
      rangeSeconds: $rangeSeconds
    ) {
      managedServiceId
      kind
      name
      rangeSeconds
      series {
        name
        unit
        source
        samples {
          ${TIME_SERIES_POINT_FIELDS}
        }
      }
    }
  }
`;

/**
 * Per-pod CPU + memory time-series + restart history (#713).
 *
 * Powers the pod-row expander on the Observability tab. Returns null
 * when the cluster has no Prometheus endpoint or the app/pod doesn't
 * resolve — the FE renders the "metrics not flowing" callout.
 */
export const GET_POD_RESOURCE_USAGE = gql`
  query GetPodResourceUsage(
    $appSlug: String!
    $podName: String!
    $environmentName: String
    $rangeSeconds: Int
  ) {
    astroliftPodResourceUsage(
      appSlug: $appSlug
      podName: $podName
      environmentName: $environmentName
      rangeSeconds: $rangeSeconds
    ) {
      reason
      podName
      rangeSeconds
      restartCount
      lastRestartAt
      samples {
        ts
        cpuCores
        memoryBytes
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
/**
 * Per-endpoint HTTP metrics (#641).
 *
 * Backed by the cluster Prometheus stack; one row per route with rate,
 * error ratio, and p50/p90/p99 latency. Empty list when OTEL HTTP
 * instrumentation isn't detected on the workload.
 */
export const GET_APP_ENDPOINT_METRICS = gql`
  query GetAppEndpointMetrics(
    $appSlug: String!
    $environmentName: String
    $rangeSeconds: Int
    $workloadSlug: String
  ) {
    astroliftAppEndpointMetrics(
      appSlug: $appSlug
      environmentName: $environmentName
      rangeSeconds: $rangeSeconds
      workloadSlug: $workloadSlug
    ) {
      route
      requestRate
      errorRateRatio
      p50Ms
      p90Ms
      p99Ms
    }
  }
`;

/**
 * Trace explorer list + span detail (#644).
 *
 * `astroliftAppTraces` returns the top N recent traces; the panel
 * lazy-loads `astroliftTraceSpans` per trace on row expansion.
 */
export const GET_APP_TRACES = gql`
  query GetAppTraces(
    $appSlug: String!
    $since: String!
    $until: String!
    $environmentName: String
    $service: String
    $status: String
    $limit: Int
  ) {
    astroliftAppTraces(
      appSlug: $appSlug
      since: $since
      until: $until
      environmentName: $environmentName
      service: $service
      status: $status
      limit: $limit
    ) {
      traceId
      rootService
      rootOperation
      spanCount
      durationMs
      statusCode
    }
  }
`;

export const GET_TRACE_SPANS = gql`
  query GetTraceSpans($appSlug: String!, $traceId: String!, $environmentName: String) {
    astroliftTraceSpans(appSlug: $appSlug, traceId: $traceId, environmentName: $environmentName) {
      traceId
      spanId
      parentSpanId
      operation
      service
      startTime
      durationMs
      statusCode
      attributes
    }
  }
`;

/**
 * The metric names this app's own workloads expose (#1226).
 *
 * Feeds the Query panel's picker: an operator cannot write PromQL against
 * a metric they do not know the name of, and the platform families that
 * share the namespace (cAdvisor, kube-state-metrics, the ingress
 * controller) are filtered out server-side because those already have
 * their own panels.
 *
 * ``truncated`` means the app emits more distinct names than ``limit``.
 * Worth rendering rather than swallowing: an app minting a metric name
 * per request id looks exactly like a richly instrumented one from a
 * capped list.
 */
export const APP_METRIC_NAMES = gql`
  query AppMetricNames($appSlug: String!, $environmentName: String, $limit: Int) {
    astroliftAppMetricNames(appSlug: $appSlug, environmentName: $environmentName, limit: $limit) {
      ok
      error
      names
      truncated
      limit
    }
  }
`;

/**
 * Ad-hoc PromQL executor (#647).
 *
 * Returns an envelope with ok / error so the panel can surface
 * compile-time and transport errors without bubbling a GraphQL error.
 */
export const EXECUTE_PROMQL = gql`
  query ExecutePromql(
    $appSlug: String!
    $query: String!
    $startUnix: Int!
    $endUnix: Int!
    $stepSeconds: Int!
    $environmentName: String
  ) {
    astroliftExecutePromql(
      appSlug: $appSlug
      query: $query
      startUnix: $startUnix
      endUnix: $endUnix
      stepSeconds: $stepSeconds
      environmentName: $environmentName
    ) {
      ok
      error
      series {
        metricLabels
        values {
          ts
          value
        }
      }
    }
  }
`;

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
      reason
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
