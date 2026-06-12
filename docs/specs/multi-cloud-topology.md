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
- **Labels**: operator-defined key-value pairs for placement targeting.
  Standard keys: `env`, `region`, `provider`, `tier`, `zone`.
- **Status**: derived from API server reachability checks. One of
  `healthy`, `degraded`, `unreachable`, `decommissioned`.
- **Capabilities**: detected at registration and on each reconcile.
  Includes: `cert_manager`, `ingress_controller`, `metrics_server`,
  `prometheus`, `external_dns`, `storage_class`.

Clusters do not know about each other. There is no cluster-to-cluster
peering, no shared mesh, and no cross-cluster service discovery at the
platform level. Astrolift coordinates across clusters from the control
plane only.

---

## App placement

An app can be deployed to any registered cluster, to multiple clusters
simultaneously, or exclusively to clusters that match a placement
policy.

### Manual placement

The default. When a deploy is triggered, the operator selects the
target cluster from the list of registered clusters. Any registered
cluster is eligible by default.

### Placement policies

An org-level placement policy is a set of label selector rules that
constrain which clusters an app may be deployed to. Policies are
defined in **Administration → Policies → Placement** and assigned
to projects or individual apps.

Example policy structure:

```
name: production-us-only
rules:
  - matchLabels:
      env: production
      provider: aws
      region: us-west-2
```

An app with this policy applied can only be deployed to clusters that
match all three label conditions. The deploy wizard surfaces only
matching clusters.

### Multi-cluster deploy

Astrolift supports deploying the same app version to multiple clusters
in a single operation. Each cluster gets its own independent Deploy
record and lifecycle. From the platform's view, a "multi-cluster
deploy" is N independent deploys that share a source and manifest
snapshot.

To trigger a multi-cluster deploy:

```graphql
mutation {
  deployApp(
    appSlug: "<slug>"
    clusterSlugs: ["prod-us-west-2", "prod-eu-west-1"]
  ) {
    ok
    deployments { id clusterSlug status }
    errors { field messages }
  }
}
```

The CLI equivalent:

```bash
astro deploy --app <slug> --cluster prod-us-west-2 --cluster prod-eu-west-1
```

Each deploy is independently observable, independently rollbackable,
and independently fails or succeeds.

---

## Zentinelle per-cluster enforcement

Zentinelle is Astrolift's policy enforcement layer. In a multi-cloud
topology, each registered cluster has its own Zentinelle agent instance
running as a Kubernetes admission webhook in the cluster. The
Zentinelle agent:

- Enforces the org's policy set for workloads targeting that cluster.
- Runs locally inside the cluster — it does not make outbound calls
  to the Astrolift control plane for admission decisions.
- Syncs its policy bundle from the control plane on a configurable
  interval (default: 60 seconds). Policy changes propagate to all
  clusters within that interval.
- Operates independently of the Astrolift control plane's availability.
  If the control plane is unreachable, Zentinelle continues enforcing
  the last-synced policy bundle.

This means policy enforcement is consistent across clouds with no
single point of failure tied to the control plane's connectivity.

---

## Federated observability

Astrolift aggregates observability data from all registered clusters
into a single pane of glass. The aggregation model:

- **Logs**: each cluster's app workloads write to the cluster-local
  logging stack (Loki, CloudWatch, Cloud Logging, etc. depending on
  the provider driver). Astrolift proxies log queries through the
  provider driver at read time — it does not replicate logs to a
  central store.
- **Metrics**: if in-cluster Prometheus is detected, Astrolift proxies
  metric queries to it. Cross-cluster metric federation (a single
  Prometheus query across multiple clusters) is not supported in this
  version.
- **Events**: Kubernetes events for app workloads are scraped by the
  Astrolift cluster agent and stored in the control plane database.
  They are queryable across all clusters from the platform UI.
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
deploy time. The same secret value reaches all clusters the app is
deployed to. Cluster-specific overrides are not supported in this
version — if different clouds need different values, use separate app
registrations or environment-variable override in the manifest.

### Cost allocation

Astrolift's cost estimation is provider-specific. For each cluster,
cost estimates use the provider driver's pricing API for that cloud.
Cross-cloud aggregate cost reports sum the estimates from all clusters
in the org. Accuracy depends on each provider's pricing API coverage.

---

## Cluster labeling conventions

Consistent labels make placement policies predictable. Recommended
baseline labels for every registered cluster:

| Key | Example values | Purpose |
|-----|---------------|---------|
| `env` | `production`, `staging`, `development` | Environment tier |
| `provider` | `aws`, `gcp`, `azure`, `k8s_native` | Cloud provider |
| `region` | `us-west-2`, `europe-west1`, `eastus` | Cloud region |
| `tier` | `standard`, `gpu`, `spot` | Node type or cost tier |
| `org-unit` | `platform`, `data`, `frontend` | Internal team ownership |

Labels are freeform strings. The conventions above are suggestions;
the platform enforces no label schema. Placement policies reference
whatever label keys the org uses.

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
- **No cluster-local secret overrides**: the same secret value goes to
  all clusters. Per-cluster overrides are on the roadmap.

---

## Related

- [Cluster management runbook](../runbooks/cluster-management.md)
- [Cluster prerequisites](../operators/cluster-prerequisites.md)
- [Demo sample app topologies](../demo-apps/)
