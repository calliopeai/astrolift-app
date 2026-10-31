# Multi-cloud topology spec

This document describes how Astrolift models and manages workloads that
span more than one cloud provider or region. It covers the conceptual
model, cluster labeling conventions, app placement policies, and
cross-cluster operational considerations.

---

## Overview

Astrolift is a cloud-agnostic control plane. A single Astrolift install
can register Kubernetes clusters from AWS EKS, Google GKE, Azure AKS,
and self-managed Kubernetes simultaneously. From the platform's
perspective, every registered cluster is an equally-valid deployment
target — the provider identity shapes which drivers are used for
managed services and which cloud-native integrations (ALB, Cloud DNS,
Azure Container Registry, etc.) are available, but it does not change
the deployment model.

The control plane itself runs outside any one cloud. It does not have
a home cloud. Its database and object storage are backed by whatever
the operator chose at install time, but those choices do not restrict
which clouds tenant clusters can run on.

---

## Cluster model

A cluster in Astrolift is a registered Kubernetes API endpoint. The
model holds:

- **Provider**: the cloud driver used for this cluster's managed
  services and native integrations (`aws`, `gcp`, `azure`,
  `k8s_native`).
- **Region**: the cloud region or datacenter the cluster runs in
  (e.g. `us-west-2`, `europe-west1`, `eastus`). Freeform string —
  the platform does not validate it against a provider's region list.
- **Node labels** (`node_labels`): operator-defined strings matched
  case-insensitively against an agent's `runs_on` at dispatch
  (e.g. `gpu`, `high-memory`, `spot`). There is no cluster label field
  for app placement.
- **Lifecycle**: one of `registered`, `managing`, `managed`, `error`,
  `decommissioning`, `decommissioned`. **Heartbeat status**, derived
  from the cluster agent's last heartbeat: `never_seen`, `connected`,
  `degraded`, `offline`.
- **Capabilities**: probed when the cluster is brought into management
  and on `ReconcileClusterCapabilitiesWorkflow` (every 10 minutes).
  Includes: `kubernetes_version`, `cert_manager_installed`,
  `external_dns_installed`, `external_secrets_installed`,
  `gateway_api_installed`, `vpa_installed`, `cnpg_installed`,
  `pod_security_admission_level`, `network_policy_engine`,
  `operator_versions`, `installed_crds`.

Clusters do not know about each other. There is no cluster-to-cluster
peering, no shared mesh, and no cross-cluster service discovery at the
platform level. Astrolift coordinates across clusters from the control
plane only.

---

## App placement

An app can be deployed to any registered cluster, or to several, one
environment per cluster.

### Placement by environment

Placement is a property of the app environment, not of the deploy. An
`AppEnvironment` binds one app to one `TenantCluster` (and optionally a
managed domain); a deploy targets an environment and lands on that
environment's cluster. Create an environment on a given cluster with:

```bash
astro app environments create <name> --cluster <cluster-slug>
```

There is no org-level placement policy: any registered cluster the
caller can see is eligible when the environment is created.

### Multi-cluster deploy

To run the same app on several clusters, give it one environment per
cluster and deploy each. Each environment gets its own independent
Deployment record and lifecycle; from the platform's view, a
"multi-cluster deploy" is N independent deploys that share a source and
manifest snapshot. One `startDeployment` per environment:

```graphql
mutation {
  startDeployment(
    input: { appSlug: "<slug>", environmentName: "prod-us-west-2" }
  ) {
    ok
    data { id status }
    errors { field message }
  }
}
```

The CLI equivalent (the app slug comes from `--app` or the local
`astrolift.toml`):

```bash
astro app deploy --env prod-us-west-2
astro app deploy --env prod-eu-west-1
```

Each deploy is independently observable, independently rollbackable
(`astro app rollback --env <name>`), and independently fails or
succeeds.

---

## Zentinelle per-cluster enforcement

Zentinelle is the agent governance control plane; the gateway it governs
agents through runs inside each cluster as a data plane. In a multi-cloud
topology:

- An organization connects to one Zentinelle (`ZentinelleConnection`,
  Zentinelle URL plus a one-time enrollment code); shared clusters use
  the platform operator's install connection.
- Each registered cluster is registered with Zentinelle through that
  connection and receives its own gateway credential, written straight
  into a Secret in the cluster.
- Enforcement actions reach Astrolift through the install-scoped
  enforcement feed, polled by task reconciliation, runner callbacks and
  the box reaper, or pushed to
  `POST /app/zentinelle/agent-enforcement/`. See
  [agent-enforcement.md](../agent-enforcement.md).

Zentinelle governs agent runs; it is not a Kubernetes admission webhook
and does not gate app workloads.

---

## Federated observability

Astrolift aggregates observability data from all registered clusters
into a single pane of glass. The aggregation model:

- **Logs**: live tail streams per pod through the driver's
  `stream_logs` (kubelet API). Historical, time-range queries need the
  aggregator the operator configured on the cluster's `provider_config`
  (`log_driver` plus `log_config`: Loki, CloudWatch, Cloud Logging or
  Azure Monitor); without one the UI shows live tail only. Astrolift
  proxies log queries at read time; it does not replicate logs to a
  central store.
- **Metrics**: if a Prometheus endpoint is configured
  (`provider_config["prometheus_endpoint"]`) or discovered by the
  capability probe, Astrolift proxies PromQL queries to it. Cross-cluster
  metric federation (a single Prometheus query across multiple clusters)
  is not supported in this version.
- **Events**: Kubernetes events for app workloads are read from each
  cluster at query time through the driver's `list_events` (Warning
  events by default); they are not stored in the control plane database.
- **Traces**: OpenTelemetry traces are collected per-cluster and
  queryable from the **Traces** surface. Cross-cluster trace
  correlation is possible if the application propagates trace context
  across cluster boundaries.

---

## Cross-cloud considerations for operators

### Networking

Clusters on different clouds have no automatic network path between
them. If an app running on cluster A needs to call a managed service
(database, cache) provisioned on cluster B's cloud, the service must
be routable over the public internet (or via a VPN / interconnect
the operator manages separately).

Astrolift does not provision cross-cloud network links. Managed
services are provisioned in the same cloud and region as the cluster
that requests them, and the resulting connection strings are injected
only into apps on that cluster.

### DNS and TLS

Each cluster has its own DNS zone and ingress endpoint. An app deployed
to three clusters has three ingress entries under three subdomains
(one per cluster's base domain), unless the operator uses a global
load balancer (AWS Global Accelerator, GCP Cloud Load Balancing,
Cloudflare) to route a single hostname to multiple cluster endpoints.
Astrolift does not configure global load balancers; the operator is
responsible for that layer.

TLS is managed per-cluster by cert-manager. Each cluster's cert-manager
issues its own certificates for the domains it serves.

### Secrets and configuration

App environment variables and secrets are stored in the Astrolift
control plane and injected into each cluster's workload namespace at
deploy time. A secret's scope is `all`, one environment name, or
`preview:<branch>` (`astro app secrets create --scope <scope>`); since
an environment is bound to one cluster, a per-environment scope is the
per-cluster override.

### Resource usage and infrastructure attribution

Resource quotas and workload CPU/memory observations remain scoped to their
organization and cluster. Astrolift does not expose provider pricing estimates
or cross-cloud monetary reports. Private provider-native infrastructure actual
adapters remain unused by public APIs and collectors; their raw tags do not
establish project ownership or complete attribution. See the
[monetary retirement procedure](../operators/retire-costing.md) for retained
historical records and the required worker/schedule transition.

---

## Cluster labeling conventions

`node_labels` are free strings matched against an agent's `runs_on`;
there is no key-value cluster label field or placement policy. The
conventions below are for naming consistency only:

| Key | Example values | Purpose |
|-----|---------------|---------|
| `env` | `production`, `staging`, `development` | Environment tier |
| `provider` | `aws`, `gcp`, `azure`, `k8s_native` | Cloud provider |
| `region` | `us-west-2`, `europe-west1`, `eastus` | Cloud region |
| `tier` | `standard`, `gpu`, `spot` | Node type or cost tier |
| `org-unit` | `platform`, `data`, `frontend` | Internal team ownership |

Labels are freeform strings. The conventions above are suggestions;
the platform enforces no label schema.

---

## Limitations (current version)

- **No active-active failover**: Astrolift does not monitor app health
  across clusters and re-route traffic automatically. Multi-cluster
  deploy is a deployment fan-out, not a high-availability mechanism.
- **No cross-cluster service mesh**: Astrolift does not configure
  Istio, Linkerd, or any other mesh to connect pods across clusters.
- **No global DNS management**: Astrolift creates DNS records in the
  DNS zone associated with each cluster's base domain. Global
  hostname routing is out of scope.
- **No metric federation**: cross-cluster PromQL queries are not
  supported.
- **No cluster-level secret scope**: secrets are scoped per
  environment, not per cluster; an app with two environments on one
  cluster carries two scopes.

---

## Related

- [Cluster management runbook](../runbooks/cluster-management.md)
- [Demo sample app topologies](../demo-apps/)
