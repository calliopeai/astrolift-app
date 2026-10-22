# App network I/O

`astroliftAppNetworkMetrics` reports the bytes per second an app
environment's pods, or one workload's verified pods, received and sent, and
the bytes moved in the window (#2271). The web console shows it as the
Network I/O card in the golden signals panel (Logs & metrics › Metrics). The
read requires `app.read` on the app, including the bearer token's team and
permission ceilings, exactly as `astroliftAppGoldenSignals` does.

```graphql
query Network($app: String!, $environment: String, $workload: String) {
  astroliftAppNetworkMetrics(
    appSlug: $app
    environmentName: $environment
    workloadSlug: $workload
    rangeSeconds: 3600
  ) {
    reason
    rangeSeconds
    stepSeconds
    rateWindowSeconds
    sourcedAt
    series {
      direction
      unit
      totalBytes
      reason
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
      }
    }
  }
}
```

```sh
astro api graphql --file network.graphql \
  --var app=example --var environment=production --var workload=api
```

## Source and units

- The kubelet's cAdvisor counters `container_network_receive_bytes_total`
  and `container_network_transmit_bytes_total`, read from the environment's
  cluster Prometheus (the cluster's `prometheus_endpoint`, as for the golden
  signals). Prometheus must scrape the kubelet's `/metrics/cadvisor`, the
  same scrape CPU and memory saturation need. `source` is `CADVISOR`.
- cAdvisor counts a pod's network once, on the pod sandbox that holds its
  network namespace; every interface in that namespace is summed, loopback
  excluded.
- `samples` are bytes per second (`unit: bytes_per_second`): a `rate()`
  over `rateWindowSeconds`, never under two minutes because cAdvisor is
  scraped about every 30 seconds, one point per `stepSeconds`.
- `totalBytes` is an `increase()` over the measured window: the whole range
  for an environment, from `measurementStart` for a workload.
- `sourcedAt` is when the control plane read Prometheus. Sample timestamps
  are the collector's evaluation times; the last one is the freshest data.
- `promql` is the rate expression the server ran.

## Scope

| Selection                                                 | Effective scope              | Identity basis     | Reads                                        |
| --------------------------------------------------------- | ---------------------------- | ------------------ | -------------------------------------------- |
| No workload                                               | `APP_ENVIRONMENT`            | `NAMESPACE`        | Every pod in the environment's own namespace |
| A workload                                                | `WORKLOAD`                   | `VERIFIED_RUNTIME` | Only the workload's current, verified pods   |
| Missing or unverifiable target, or no Prometheus endpoint | Requested scope, unavailable | `UNRESOLVED`       | Nothing                                      |

A workload's pods come from the same membership read as workload CPU and
memory (#2263): identity labels and the controller chain. Every series must
carry the environment's namespace, a verified pod's name and that pod's UID
in its cgroup `id` (`pod<uid>/...` under the cgroupfs driver,
`pod<uid with underscores>.slice/...` under systemd). A series of any other
pod, or of an earlier pod with the same name, refuses the read
(`INVALID_DATA`); two series for one pod refuse it (`AMBIGUOUS_SERIES`); a
pod with no series, or a gap at any step, makes it `PARTIAL_DATA`. The
measurement starts once the newest current container has a full rate window,
as for workload CPU and memory: a container restart moves `measurementStart`
even though the pod's network counters carry on, and until one rate window
has passed the read is `NO_DATA_YET`. A workload never falls back to the
namespace total.

## Reasons

Each direction carries a panel `reason` and a cause in
`measurement.unavailableReason`. An unavailable direction has no samples and
`totalBytes: null`; nothing unmeasured is reported as zero. The envelope's
`reason` is `OK` when either direction has samples, `ERROR` when either
failed, otherwise the directions' shared reason.

| `reason`                    | `unavailableReason`                                                                      | Meaning                                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `NOT_CONFIGURED`            | `NOT_CONFIGURED`                                                                         | The cluster has no Prometheus endpoint.                                       |
| `NOT_CONFIGURED`            | `ENVIRONMENT_NOT_FOUND`, `WORKLOAD_NOT_FOUND`                                            | The app has no live environment or workload by that name.                     |
| `NOT_CONFIGURED`            | `TARGET_NOT_OWNED`                                                                       | The environment's cluster is missing, deleted, inactive or another org's.     |
| `NOT_SUPPORTED_BY_PROVIDER` | `NOT_SUPPORTED_BY_PROVIDER`                                                              | The cluster's driver cannot verify workload membership; read the environment. |
| `NO_DATA_YET`               | `NO_PODS`, `NO_DATA_YET`                                                                 | No pods, pods younger than a rate window, or no samples in the window.        |
| `ERROR`                     | `QUERY_ERROR`                                                                            | Prometheus failed, refused the query or answered incompletely.                |
| `ERROR`                     | `OWNERSHIP_UNVERIFIED`                                                                   | The pods do not carry the workload's current identities; redeploy it.         |
| `ERROR`                     | `PERMISSION_DENIED`, `PROVIDER_ERROR`, `PARTIAL_MEMBERSHIP`, `MEMBERSHIP_LIMIT_EXCEEDED` | The membership read failed, or passed its 64-pod, 128-container limit.        |
| `ERROR`                     | `INVALID_DATA`, `AMBIGUOUS_SERIES`, `PARTIAL_DATA`                                       | The collector's series do not match the verified pods.                        |

Another organization's app slug is refused at the permission gate, or, when
an org-wide grant clears the gate, answers `NOT_CONFIGURED` with no series.
No cluster is read either way.

## Not available

- Packets, drops, errors and a per-interface breakdown.
- A pod on the host network reports the node's interfaces under its own
  name. Astrolift renders no host-network workloads, and this read does not
  detect one.
- HTTP request traffic, status codes and latency are separate reads:
  `astroliftAppGoldenSignals` and `astroliftAppStatusCodeBreakdown`. They
  need an ingress metrics mapping or `metrics.enabled` instrumentation; see
  [workload golden signals](./workload-golden-signals.md).
