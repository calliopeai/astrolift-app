# Model catalogue and observations (#2214)

`astroliftHuggingFaceModels` reads the public Hub API on the server. It accepts
search, author, pipelineTag, library, license, gated and sortBy filters. Supported
sorts are downloads, likes, lastModified, createdAt and trendingScore. `first` is
1–30; `after` is a signed, caller/filter-bound cursor valid for 20 minutes. One
request fetches one page. The fixed HTTPS origin, no redirects, five-second
timeout and 1 MiB response limit apply to search and detail. No caller URL,
browser API key, saved Hub token, model file download or remote code execution is
part of this read.

`astroliftHuggingFaceModel(repoId, revision)` resolves a repository revision to
its actual immutable SHA. A missing SHA or mismatching requested commit fails
closed. Search metadata may omit SHA; resolve detail before deployment. The
catalogue exposes safe upstream task, architecture, licence and gated metadata,
not raw cards/configuration. Licence metadata is not legal approval, and gating
does not prove accepted terms or download access. Compatibility is UNKNOWN;
architecture/task tags do not prove support, CPU/GPU placement or hardware fit.

AVAILABLE, NO_DATA, RATE_LIMITED and UNAVAILABLE distinguish observations from
transport failure. Missing metadata is nullable, never filled with invented
counts. Downloads and likes are public Hub counts, not serving usage. Retry
delays only come from an actual bounded numeric upstream Retry-After header.
An observation timestamp is the read attempt time, not an upstream freshness
guarantee. Reads require a live authenticated account; bearer sessions retain
their actual live organization membership and credential validity. Catalogue
access grants no model/cluster/subscription authority.

Primary contracts: [Hub API client](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api),
[official search/pagination implementation](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/hf_api.py),
[gated repositories](https://huggingface.co/docs/hub/models-gated).

## Deployment observations

`astroliftModelDeploymentMetrics(serviceId, expectedClusterId, expectedProviderId,
start, end)` checks immutable identities, current coherent owner, credential
ceiling and actual environment policy before configuration, cache or HTTP.
Cluster-owned aggregate metrics require explicit organization CLUSTER_REGISTER;
subscribers' APP_READ_METRICS does not grant aggregate traffic. Legacy app/project
models retain their actual APP_READ_METRICS ancestry and environment facts. The
current bearer catalog requires `admin` for CLUSTER_REGISTER; `read:clusters`
does not silently widen that permission. Models with an inactive/unmanaged
cluster or disabled provider remain inspectable but perform no transport.

Windows are 1 minute–24 hours, timezone-aware and not in the future. Two fixed
Prometheus requests use five-second timeouts, 1 MiB bodies, 120 samples/series
and at most 15 known series total. Each series reports source, unit, observation
time and its aggregation window (300 seconds for rates/p95s, zero for gauges).
STALE means its last sample predates the requested end by more than the greater
of 120 seconds or twice the sampling step. Historical windows measure their
own requested period, not current health. Unknown/non-finite/negative or
mismatched series fail their request group; another successfully observed group
can remain available. No diagnostic body or raw labels are exposed.
Undefined histogram quantiles (zero recorded events) are filtered by a
non-negative PromQL comparison and reported as NO_DATA, preserving genuine
zero request counters. A non-finite value returned in the HTTP matrix still
fails its group rather than becoming zero.

vLLM selectors use the exact persisted service GUID, canonical namespace and
resource name. CPU/memory and pod observations join kube-state-metrics pod
labels for that GUID and instance; require the install to allowlist
`astrolift.io/managed-service-id` and `app.kubernetes.io/instance`. An absent
mapping is NO_DATA; no namespace-wide fallback occurs. `successful_requests`
measures successful requests, not all attempts. KV-cache usage measures cache
blocks, never GPU utilization or device VRAM. GPU utilization and VRAM usage
are UNSUPPORTED without verified device-to-pod attribution. App usage is not
inferred from deployment aggregate counters.

GraphQL owner gating is one boundary. A shared model runtime must separately
protect its `/metrics` path with an operator-only credential and authenticate
its ServiceMonitor; an app NetworkPolicy or this resolver alone cannot prevent
subscribers scraping an open model API port.

## Tenant shared-model density

`astroliftClusterModelDensity(clusterId, expectedProviderId, start, end)` uses
the same owner-level organization gate. Scope is explicitly
`organization_cluster_owned_models`, excluding legacy app/project models and
other tenants. This is shared-model inventory, not global cluster saturation.
`modelCount` is the exact authorized inventory count. At most 20 immutable-GUID
ordered rows return; returnedCount/truncated/inventoryLimit disclose the bound.
Up to five fleet form-POST requests (four exact targets each) observe running
and ready pods, CPU/memory usage and CPU/memory requests with three-second
timeouts, 64 KiB encoded requests, 512 KiB responses, 24 series/request and
120 samples/series. POST avoids URL/header limits for these fixed identity-bound
expressions. An exceeded bound marks the batch UNAVAILABLE, never a silently
truncated measurement. Desired/applied requests are not observed usage totals.

Desired requests come from persisted config; their timestamp is the row's
recorded modification time, not infrastructure observation. Applied requests
come only from provider-confirmed applied_config and the readiness observation
recorded alongside that configuration, both nullable. A later operation failure
does not refresh the applied snapshot's observation time. Historical applied
configuration without recorded observation provenance has a null timestamp.
Supported quantities and explicitly present replica/device
counts may be multiplied; absent or unsupported values stay null. Inactive
configured rows are not running instances. Desired/applied replica counts do
not establish observed Deployment generation or successful reconciliation.

An organization-owned cluster can expose its timestamped GPU capability probe
by device resource (including distinct MIG profiles). The probe is STALE after
30 minutes, with that threshold explicit. CPU/memory/device VRAM capacity is
unavailable from this probe and stays null. On install-shared physical hardware,
capacity is UNSUPPORTED without a verified tenant node-pool mapping; no other
tenant's hardware or namespace aggregate is projected. A measured zero remains
zero, while missing probe data remains NO_DATA.

Reads are absent from the curated public schema. Discovery capabilities report
API support, not authorization or telemetry availability. No live provider,
model weights, inference, account-wide certificate/config or credential reads
are part of these observations.

Sources: [vLLM production metrics](https://docs.vllm.ai/en/stable/usage/metrics/),
[Kubernetes object-state metrics](https://kubernetes.io/docs/concepts/cluster-administration/kube-state-metrics/),
[pod labels/metric allowlists](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/workload/pod-metrics.md),
[Prometheus Operator target relabeling](https://prometheus-operator.dev/docs/api-reference/api/),
[Prometheus query transport](https://github.com/prometheus/prometheus/blob/main/docs/querying/api.md),
[histogram quantile semantics](https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile).

## Models navigation capability

`me.modules` includes an always-enabled `models` row, independent of Agents.
`canView` requires the same explicit-ORG `org.read` and credential ceiling as
the shared model collection, or `app.read` at any actual live authorized app
for the legacy catalogue (which may still be empty). The legacy fallback uses
canonical app ownership, policy visibility, shares and the bearer team ceiling.
`canCreate`, `canManage` and `canRun` require `cluster.update` at the current
organization, matching shared model management and advisory/prompt admission.
A team/project/app grant containing an organization slug cannot grant upward.

These advisory capabilities reload the current account, organization,
membership and bearer validity before evaluating Models access. Platform
superusers retain their existing active-account bypass; bearer membership and
scope/team ceilings still apply. Other module rows retain their contracts.
An entitlement does not replace the selected target's actual policy, immutable
identity, readiness or mutation checks.
