# Per-edge topology traffic

`astroliftTopologyTraffic(appSlug, start, end, environmentName)` returns measured
intra-app traffic. Omit `environmentName` to read every live environment, or pass
an exact persisted name for one environment. `start` and `end` must include a
timezone, span one minute to 24 hours, and end at or before the current time.
Missing apps return null; a missing named environment or invalid window fails
the query instead of selecting another environment.

The query requires `app.read_metrics` on the actual live app. Home team/project
ownership, bearer scope and team ceilings apply independently of the UI's
selected headers. Persisted environment policy facts authorize every selected
environment before any backend or cache read. An app-wide request fails when
one of its environments is denied; selecting an allowed environment reads only
that environment. Every cluster must be live, active and owned by the app's
organization or be an organization-neutral shared target.

## Measured source and mapping

The initial source is Istio's documented `istio_requests_total`, queried through
the environment cluster's `prometheus_endpoint`. `observability_kind` may be
`prometheus` or `otel` using the Prometheus wire protocol. Other backends report
`UNCONFIGURED`. See [Istio standard metrics](https://istio.io/latest/docs/reference/config/metrics/)
for the counter, reporter and workload/status labels.

A destination reporter avoids counting both sides of a request. Source and
destination namespaces both match `namespace_for_environment`, including a
preview's recorded namespace. Source and destination workload names match live
registered standing controllers, whose rendered Kubernetes names equal their
manifest names. The renderer uses `astrolift.dev/app`; it does not emit Istio's
plain `app` label, so this query does not assume `source_app` or `destination_app`.
Names held by another app in the same cluster and namespace are excluded, even
when the caller has an organization-wide grant. Two environments of one app
sharing a legacy namespace report `UNCONFIGURED`, because these metric labels
cannot distinguish their traffic.

Each edge includes source/destination workload IDs and names, observed samples,
mean `requestRate` and `errorRate` in requests per second, and `errorRatio` as a
fraction. Samples sum five-minute counter rates across response statuses for
that directed edge. HTTP 4xx/5xx and nonzero gRPC statuses count as errors. Missing
or unrecognized status labels report `UNAVAILABLE`. Means use only the returned
sample timestamps; gaps are not filled with zero. A measured zero remains zero.
External ingress, cross-app or cross-namespace traffic, ephemeral jobs, TCP
connections and cloud-managed service calls are outside this initial source.

## Empty states and bounds

| Status | Meaning |
| --- | --- |
| `AVAILABLE` | At least one measured edge in the environment |
| `NO_DATA` | Successful read with no eligible measured edges |
| `UNCONFIGURED` | No supported endpoint or indistinguishable environment namespace |
| `UNAVAILABLE` | Transport, invalid readings, malformed matrix or response limit failure |
| `PARTIAL` | Mixed environment states or explicit edge truncation |

No state produces synthetic traffic. An app without environments is
`UNCONFIGURED`. Each environment has its own status, optional reason and
`truncated` flag, so an unavailable environment cannot be mistaken for zero
traffic in an otherwise successful app read.

Requests cover at most eight environments and 64 standing workloads. Larger
apps must select an environment, or narrow their standing topology. One request
per selected environment has a five-second timeout, two-MiB response limit,
2,048 matrix-series limit, and 120-sample limit per series and output edge.
Step size is at least 30 seconds and grows to keep the requested window bounded.
The app result contains at most 256 edges, sorted by source/destination within
each environment, with explicit `edgeLimit`, `sampleLimit`, `environmentLimit`
and `truncated` metadata. Backend limit violations return `UNAVAILABLE` rather
than silently turning partial readings into complete traffic. Successful scoped
reads use the existing 30-second Prometheus cache; permission or policy revocation
still fails before cache access. Clients never supply raw PromQL.
