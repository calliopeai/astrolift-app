# Workload golden signals

`astroliftAppGoldenSignals` reports the effective scope, source, canonical target
and availability of **each** signal. Select the app, recorded environment and
workload explicitly. A missing environment or workload never falls back to a
sibling. The read requires current app read permission, including the bearer
token's permission ceiling.

```graphql
query Signals($app: String!, $environment: String!, $workload: String!) {
  astroliftAppGoldenSignals(
    appSlug: $app
    environmentName: $environment
    workloadSlug: $workload
    rangeSeconds: 900
  ) {
    reason
    signals {
      name
      unit
      samples {
        ts
        value
      }
      measurement {
        effectiveScope
        source
        identityBasis
        available
        unavailableReason
        membershipObservedAt
        measurementStart
        target {
          organizationId
          appId
          appSlug
          environmentId
          environmentName
          clusterId
          namespace
          workloadId
          workloadSlug
        }
        containers {
          podName
          podUid
          containerName
          containerId
        }
        usageUnit
        usageSamples {
          ts
          value
        }
        limitSamples {
          ts
          value
        }
      }
    }
  }
}
```

Save that document as `signals.graphql` and use the existing public CLI:

```sh
astro api graphql --file signals.graphql \
  --var app=example --var environment=production --var workload=api
```

The CLI uses the selected server, organization and authenticated session.
Unauthorized reads exit with an error. Do not turn a failed query or empty
sample array into zero utilization. The web cards show the server's scope,
source, identity basis and unavailable reason, including mixed availability.

## Interpret scope and source

| Selection / source                                     | Effective scope              | Identity basis     |
| ------------------------------------------------------ | ---------------------------- | ------------------ |
| Workload CPU or memory, verified runtime               | `WORKLOAD`                   | `VERIFIED_RUNTIME` |
| Workload request metrics                               | `WORKLOAD`                   | `SOURCE_LABELS`    |
| Ingress or CloudWatch ALB request metrics              | `APP_ENVIRONMENT`            | `NAMESPACE`        |
| Resource namespace aggregate without workload selector | `APP_ENVIRONMENT`            | `NAMESPACE`        |
| Missing/unverifiable target                            | Requested scope, unavailable | `UNRESOLVED`       |

Workload request instrumentation uses `app`, `environment`, `workload` and
the canonical `astrolift_app_id`, `astrolift_environment_id` and
`astrolift_workload_id` labels. Configure your instrumentation with the GUIDs
returned by the API or stamped on the deployment. Slug-only historical series
are not selected, and old canonical series cannot follow a same-name workload
replacement. Workload request reads also refuse unverified runtime membership.
`SOURCE_LABELS` describes application-published labels; it does not establish
which physical container served a request. Ingress and ALB metrics are
environment totals; a workload request never borrows those totals. CloudWatch
fallback is confined to the selected environment and app rollups. CloudWatch's
existing ALB adapter supplies p50, p95 and p99; p90 remains explicitly
unavailable instead of relabeling p95.

Namespace resource aggregates retain the existing app-environment view. Their
membership is unverified: they can include namespace sidecars or operator
resources and are not evidence of one workload's usage. Select a workload for
the physical-container measurement below.

## Enable physical workload measurements

Deploy with this version's production renderer. It stamps immutable app,
environment and workload GUID labels on controller and pod-template metadata:
`astrolift.dev/app-id`, `astrolift.dev/environment-id` and
`astrolift.dev/workload-id`. It leaves Kubernetes selectors unchanged. Existing
unstamped pods require a normal redeploy; metrics reads never mutate them or
infer ownership from pod-name prefixes.

The reader requires the GUIDs on the pod and each controller in its owner-reference
chain, verifies controller UIDs, and resolves the declared workload controller.
It captures each regular container's pod UID, runtime container ID and start
time. A same-name replacement cannot adopt an old workload's pods. Pending,
terminating, incomplete or unverified membership is unavailable. Supported
native controller kinds are Deployment (including agent/workflow workloads),
StatefulSet, DaemonSet, Job/task and CronJob.

The collector must preserve:

- cAdvisor `container_cpu_usage_seconds_total` and
  `container_memory_working_set_bytes`, with `namespace`, `pod`, `container`
  and the physical cgroup `id` containing the exact runtime container ID;
- kube-state-metrics `kube_pod_container_resource_limits`, with `namespace`,
  `pod`, `container`, `uid` and `resource`, using CPU cores and memory bytes.

The [kube-state-metrics v2.15.0 pod metric contract](https://github.com/kubernetes/kube-state-metrics/blob/v2.15.0/docs/metrics/workload/pod-metrics.md)
documents the UID-bearing limit series. cAdvisor's
[Prometheus exporter implementation](https://github.com/google/cadvisor/blob/v0.52.1/metrics/prometheus.go)
defines its cgroup identity label. Check the deployed exporter contract when
upgrading or relabeling collectors. Stripping physical IDs or pod UIDs makes
this measurement unavailable. Duplicate scrapes are rejected as ambiguous;
configure one authoritative series per physical container.

The cluster's configured `prometheus_endpoint` must be reachable from the
control plane, and the Kubernetes credential must read pods and their
controllers in the recorded namespace. The production read is bounded to 64
pods, 128 regular containers and a ten-second native inventory budget.

## Understand availability and time windows

CPU uses actual rate in cores divided by CPU limits. Memory uses working-set
bytes divided by memory limits. `usageSamples`, `limitSamples` and `samples`
share timestamps; the last value is the first divided by the second. Resource
requests are never substituted for limits. Every captured container must have
one matching usage and UID-bound limit series at each returned point; missing,
zero or partial limits do not produce a healthy zero.

`measurementStart` clips history to the current captured physical containers.
CPU also waits for a complete rate window since the newest container started.
This avoids joining historical limits for a previous container incarnation.
It is a captured membership observation, not an atomic Kubernetes inventory
transaction or a historical workload census across rollouts. Collection lag
can produce `NO_DATA_YET`, `MISSING_USAGE` or `PARTIAL_DATA` until matching data
arrives. The requested full window is still reported as `rangeSeconds`.

`unavailableReason` distinguishes missing target, configuration,
instrumentation, capability, provider permission, ownership, limits, partial
data and query/provider failures. A failed CPU query does not discard a valid
memory measurement. A valid resource signal does not imply that request
instrumentation is installed or that the app is healthy. Saturation alone
does not prove CPU throttling or an imminent OOM.

Strict workload reads also refuse successful Prometheus responses containing
nonempty or malformed `warnings`: those samples cannot establish complete
evidence. The refusal is per signal and uses a static error without exposing
collector warning text. Responses without warnings or with an empty warning
array remain valid; existing non-strict aggregate reads keep their compatibility
behavior.

## Validation boundaries

The integration fixture exercises real PostgreSQL authorization, native Kind
controller ownership, cAdvisor, kube-state-metrics v2.15.0 and Prometheus
v3.13.1. It checks two workloads, distinct recorded environments in one cluster,
same-name replacement with retained logical and old-GUID HTTP counters, missing
limits, an actual unprivileged Kubernetes
credential and a collector HTTP failure with a healthy sibling signal. The
public compiled CLI is checked against authenticated Django HTTP and real
PostgreSQL, including a narrowed bearer refusing the query.

EKS, GKE and AKS delegate to the same Kubernetes membership reader through
their existing provider credential adapters. The local native tests do not
certify cloud credentials, production exporter configuration, GPU metrics,
mobile authority or native mobile presentation. Existing dedicated legacy
resource-gauge queries are separate APIs; this contract applies to golden
signals and does not make those legacy gauges verified measurements.
