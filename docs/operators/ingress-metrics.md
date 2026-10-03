# Ingress traffic metrics

App observability shows request rate, 5xx/request ratio, latency p50/p90/p95/p99
and status-code classes for the selected app environment. Apps do not need an
instrumentation endpoint when the ingress supplies metrics. Workload resource
measurements and workload instrumentation remain separate from these environment
totals.

## Existing nginx ingress

Enable metrics on the existing ingress controller's HelmRelease or Helm values:

```yaml
controller:
  metrics:
    enabled: true
    serviceMonitor:
      enabled: true
```

The native Kubernetes recipe already includes these values. Previously installed
controllers must reconcile them too; installing Prometheus alone does not turn
on the controller exporter. Confirm the metrics Service and ServiceMonitor exist,
then check Prometheus targets for a healthy controller scrape. Preserve the
existing controller chart version and other values when adding this overlay.
The Prometheus operator must select the ServiceMonitor's namespace and labels.

For a cluster registered with `ingress_class: nginx`, Astrolift selects
`nginx_ingress_controller_requests` by the app environment's namespace and
`nginx_ingress_controller_request_duration_seconds_bucket` for latency. A shared
controller load balancer cannot replace these per-namespace measurements.

## Shared Envoy edge

Install `kube-prometheus-stack` using the cluster recipe. Its additional PodMonitor
scrapes the managed edge proxy pods on their `metrics` port at
`/stats/prometheus` every 15 seconds, across namespaces. Proxy pods normally run
in `astrolift-system`, even though their owning Gateway is in `astrolift-edge`.
The monitor selects that owning Gateway so another proxy cannot join its targets.

Configure or probe a `prometheus_endpoint` reachable from the control plane.
Cluster service DNS and ClusterIPs do not work for a control plane running outside
Kubernetes. Private endpoints must permit the control-plane network to query
Prometheus; keep metrics private and do not expose its API through a public app
route. Capability probing alone does not install Prometheus.

At query time Astrolift reads the current platform-managed HTTPRoutes and verifies
their environment namespace label and backend namespaces. It selects the exact
Envoy cluster names for those routes, including hashed long route names. Namespace
prefixes and shared ALB totals are never used as per-app identity. Envoy latency
histograms are in milliseconds and are converted to seconds before display.

These route counters describe requests forwarded to an application's upstream.
OIDC redirects or edge denials that never reach an upstream are outside these
measurements. A workload-selected chart requires that workload's own verified
instrumentation; it does not borrow environment-wide edge traffic.

## Dedicated ALB ingress

The CloudWatch exporter in the cluster recipe supplies ALB metrics to Prometheus.
Astrolift can also query CloudWatch when Prometheus is missing or a request signal
has no samples. Existing Prometheus measurements are preserved. AWS credentials
must allow load-balancer discovery and CloudWatch metric reads. Percentiles use
`MetricDataQueries[].MetricStat.Stat`, including `p90`.

The CloudWatch fallback is disabled for a shared Envoy edge, whose load balancer
serves multiple apps. An absent collector stays unconfigured; empty windows and
provider/query failures remain distinct from measured zero traffic.

## Check the read

Query `astroliftAppGoldenSignals(appSlug: ..., environmentName: ...,
rangeSeconds: 3600)` without `workloadSlug` for environment ingress totals.
Inspect each signal's `measurement.source`, `measurement.target.namespace`,
`available` and `unavailableReason`, not just the panel's overall reason.
`astroliftAppStatusCodeBreakdown` uses the same exact route selection.
