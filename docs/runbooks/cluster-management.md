# Runbook: Cluster management

Procedures for bringing a Kubernetes cluster into Astrolift management,
ongoing maintenance, and controlled decommission.

Preconditions: `cluster.register` to register a cluster, `cluster.manage` to
bring it into management, refresh setup, re-run preflight or install
prerequisites, `cluster.update` for `updateTenantCluster`,
`cluster.unregister` to decommission, `cluster.read` to list and read
clusters; a shared (organization-less) registration additionally needs the
platform-operator check behind `cluster.register`. Surfaces: the console
(`/clusters`, `/clusters/new`, `/clusters/[slug]`, `/clusters/[slug]/settings`),
the `astro` CLI (`astro cluster bootstrap`, `astro operator cluster
install-agent`), GraphQL (`astroliftClustersPage`, `astroliftCluster(slug:)`,
`bringClusterIntoManagement`, `decommissionCluster`, `updateTenantCluster`),
and a shell in the backend container for `python manage.py
register_tenant_cluster` and `temporal_ops`; `kubectl` steps need the
cluster's kubeconfig, and `--auto-discover-*` needs the cluster's cloud
account reachable from the backend task role. Placeholders: `<slug>` and
`<name>` are the cluster slug and display name (`astroliftClustersPage`);
`<cluster-guid>` is `id` from `astroliftCluster(slug: "<slug>")`;
`<eks-name>` is the cloud provider's cluster name; `<path>` is the
`--request-file` `install-agent` creates on its first run and re-reads on a
retry, and `<uuid>` is the installation id it prints (`Agent installation
<id>: <status>`); `<project-namespace>` is the Kubernetes namespace of the trusted
workload; `<id>` in `updateTenantCluster` is the cluster GUID.

---

## Bring a cluster into management

### Prerequisites

Before registering the cluster, confirm the following are in place:

| Check | Command |
|-------|---------|
| cert-manager running | `kubectl get pods -n cert-manager` |
| IngressClass exists | `kubectl get ingressclass` |
| Default StorageClass | `kubectl get storageclass` |
| metrics-server healthy | `kubectl get apiservice v1beta1.metrics.k8s.io` |

The console page `/documentation/cluster-prerequisites` has install
instructions if any are missing; the preflight checks behind them are in
`backend/providers/k8s_native/preflight.py` and their failures under
[Troubleshooting](#troubleshooting).

### Registration steps

1. **Prepare the credentials.** Registration accepts one of three auth
   methods: `kubeconfig`, `exec_plugin` or `service_account_token`. The
   control plane needs permission to:
   - Create and manage `Namespace`, `Deployment`, `Service`,
     `Ingress`, `Certificate` resources in app namespaces.
   - Read `Node`, `Pod`, `Event` resources cluster-wide (for the
     cluster health dashboard).
   - Create the `astrolift-system` namespace and its ServiceAccount,
     ClusterRole and ClusterRoleBinding (applied by bring-into-management).

   For a minimum-permission kubeconfig, create a `ClusterRole` and
   `ServiceAccount` manually. On EKS, AKS or GKE the backend can discover
   endpoint, CA and auth shape itself from the backend container
   (`--auto-discover-azure` and `--auto-discover-gcp` likewise). The
   command upserts by slug.

   ```bash
   python manage.py register_tenant_cluster --slug <slug> --name <name> \
     --plugin-slug aws --auto-discover-aws --aws-cluster-name <eks-name>
   ```

2. **Register in Astrolift.**
   - Go to **Clusters → Register cluster**.
   - Fill in the name, slug, provider plugin, region, API endpoint, auth
     method and auth config (a pasted kubeconfig is wrapped as
     `{"kubeconfig": "..."}`).
   - Click **Register**. The row is created with lifecycle `registered`:
     metadata only, not yet a deploy target. A slug that already exists
     is refused.
   - **Signature:** `registerTenantCluster` answers `CONFLICT` on field
     `slug` with `cluster slug '<slug>' already registered`; an unknown auth
     method answers `VALIDATION` `authMethod must be kubeconfig | exec_plugin
     | service_account_token`.
   - **Fix:** pick another slug, or update the existing row (rotate its
     credentials as described below).
   - **Rollback:** no rollback needed; a refused registration writes nothing.

3. **Bring the cluster into management.** Click **Bring into management**
   on the cluster header. `BringClusterIntoManagementWorkflow` flips the
   lifecycle to `managing`, verifies reachability, applies the platform
   RBAC bundle, probes capabilities, runs a one-shot preflight Job in
   `astrolift-system`, then marks the cluster `managed`. A failure lands
   in `error` with the message under **Last management diagnostic**;
   **Retry bring into management** restarts the workflow. **Refresh
   setup** and **Re-run preflight** re-run it on a managed cluster.
   Lifecycle values: `registered`, `managing`, `managed`, `error`,
   `decommissioning`, `decommissioned`.
   - **Signature:** `astroliftCluster(slug:)` reports `lifecycle: error` and
     `lastManagementError` carries the step and cause, for example `cluster
     is inactive — re-activate before bringing into management`,
     `cluster <slug> was unregistered before management could run`,
     `platform RBAC apply did not complete`, `preflight Job did not
     complete`, or `mark_managed: <exc>` (truncated to 4000 characters);
     `temporal_ops describe BringClusterIntoManagement-<cluster-guid>`
     shows the failed activity.
   - **Fix:** correct what the message names (credentials, reachability,
     RBAC, the preflight Job in `astrolift-system`), then **Retry bring into
     management**.
   - **Rollback:** no rollback needed; `error` is re-entrant and the retry
     re-applies the idempotent RBAC bundle.

4. **Review capabilities and bootstrap missing components.**
   **Settings → Bootstrap recipe** lists what the probe detected
   (cert-manager, ingress controller, external-dns, storage classes,
   metrics-server, Prometheus). Select the missing components and apply;
   `InstallClusterPrereqsWorkflow` installs them as Flux HelmReleases and
   waits for readiness. The CLI alternative installs the bundled
   `astrolift-prereqs` Helm chart:

   ```bash
   astro cluster bootstrap --cluster-slug <slug> --dry-run
   astro cluster bootstrap --cluster-slug <slug>
   astro cluster bootstrap --cluster-slug <slug> --upgrade --values-file <path>
   ```

5. **Install the keep-alive agent.** `install-agent` asks the server to
   install the heartbeat agent; `agent-install-status` reads the server-side
   confirmation. The server installs and verifies the agent before its staged
   credentials activate.

   ```bash
   astro operator cluster install-agent --slug <slug> --request-file <path>
   astro operator cluster agent-install-status --install-id <uuid>
   ```

### Verification

After bring-into-management:

- The lifecycle badge shows **managed** (green) and, once the agent is
  installed, the heartbeat shows **Connected**.
- The cluster appears as a deployment target in the app deploy wizard.
- The management workflow completed:

  ```bash
  python manage.py temporal_ops describe BringClusterIntoManagement-<cluster-guid>
  ```

---

## Cluster health monitoring

The cluster detail page provides:

- **Status indicators**: real-time checks for API server reachability,
  cert-manager, ingress controller, and metrics-server.
- **Node list**: CPU and memory utilization per node.
- **Recent activity**: deploy events, scaling events, OOMKills, restarts.
- **Alerts**: any active Prometheus alert rules targeting this cluster
  (if in-cluster Prometheus is configured).

Liveness is derived from the keep-alive agent, which POSTs
`/api/clusters/v1/<guid>/heartbeat/` every `heartbeat_interval_seconds`
(default 30). The status is **Never seen** (no heartbeat yet),
**Connected** (last pulse within 1.5 intervals), **Degraded** (one missed
interval, fewer than three) or **Offline** (three missed intervals).
Offline and Never seen render the dependent tabs as an offline empty
state instead of calling the apiserver.

---

## Shared kube-prometheus access boundary

The Kubernetes bootstrap installs one shared kube-prometheus-stack. Project
observability bundles may declare monitors, rules, and Grafana dashboards, but
they do not own or delete that shared data plane.

Prometheus contains metrics for every selected project, so its raw query API is
not a tenant boundary. Astrolift omits `PROMETHEUS_URL` and `METRICS_ENDPOINT`
from workload bindings by default. Alertmanager can mutate alerts and silences;
its endpoint is operator-only and is never included in a workload binding.
Bootstrap NetworkPolicies admit Alertmanager traffic only from
`astrolift-system` and admit Prometheus traffic from `astrolift-system` plus
explicitly trusted namespaces.

An operator who accepts cluster-wide metric visibility for a trusted workload
must complete both controls:

1. Set provider config
   `kube_prometheus_allow_workload_prometheus_access=true`.
2. Apply the operator-owned namespace label:

   ```bash
   kubectl label namespace <project-namespace> \
     astrolift.io/trusted-observability-access=true
   ```

The driver refuses to emit a Prometheus binding unless both controls are
present. Tenant roles must not have permission to set labels on Namespace
objects. Removing either the provider setting or namespace label revokes the
supported access path. The cluster CNI must enforce Kubernetes NetworkPolicy;
otherwise the predictable internal Service DNS names are not a security
boundary and raw access must be blocked by an equivalent provider firewall or
service mesh policy.

Grafana remains in workload bindings for dashboard navigation but carries no
credentials. Keep Grafana authentication and authorization enabled at its own
ingress or service boundary.

---

## Rotate cluster credentials

When the kubeconfig or service account token used by Astrolift expires
or is rotated:

There is no console or API surface that replaces a registered cluster's
stored `auth_config`: `updateTenantCluster` changes region, endpoint,
ingress class, ingress mode and ingress auth only, and
`registerTenantCluster` refuses an existing slug.

1. For an EKS, AKS or GKE cluster, re-run the registration from the
   backend container (or `--auto-discover-azure` / `--auto-discover-gcp`).
   The command upserts the row by slug and rewrites endpoint, CA and auth
   shape from the cloud API.

   ```bash
   python manage.py register_tenant_cluster --slug <slug> --auto-discover-aws --aws-cluster-name <eks-name>
   ```
2. For a pasted kubeconfig or service-account token there is currently
   no supported rotation path.
3. Click **Refresh setup** on the cluster header; the workflow
   re-verifies reachability and lands in `error` if the credentials do
   not work.

Do not revoke the old credentials until the refresh succeeds.

- **Signature:** after **Refresh setup** the cluster lands in `lifecycle:
  error` with `lastManagementError` naming the reachability or RBAC step
  (see step 3 above).
- **Fix:** re-run `register_tenant_cluster` against the correct cloud
  cluster name, then **Refresh setup** again.
- **Rollback:** the command upserts by slug; re-run it with the previous
  values to restore the prior endpoint and auth shape.

---

## Scale cluster capacity

Astrolift manages app workloads, not cluster nodes. Node scaling is
outside the platform scope — use your cloud provider's autoscaler
(Cluster Autoscaler on AWS/GCP/Azure, or Karpenter on EKS). Astrolift
surfaces HPA-based pod autoscaling per app.

When you add nodes to the cluster, Astrolift's next reconcile cycle
picks them up automatically. No action required in the platform.

---

## Enable shared S3-compatible object storage

The `k8s_native` provider can provision project and app buckets through the
SeaweedFS Operator. The operator and one shared `Seaweed` storage cluster are
cluster add-ons; Astrolift manages `Bucket` and S3 IAM resources inside that
cluster and never creates or deletes the storage engine per bucket.

Install a current [SeaweedFS Operator](https://github.com/seaweedfs/seaweedfs-operator),
enable the standalone `spec.s3` gateway on the shared `Seaweed` resource, and
configure these provider settings:

| Setting | Default | Purpose |
|---------|---------|---------|
| `seaweed_namespace` | `astrolift-storage` | Namespace containing the shared cluster and bucket/IAM CRs |
| `seaweed_cluster_name` | `astrolift-object-store` | Name of the existing `Seaweed` resource |
| `seaweed_s3_endpoint` | generated Service URL | Explicit internal or external S3 gateway URL |
| `seaweed_s3_scheme` / `seaweed_s3_port` | `http` / `8333` | Generated Service URL transport |
| `seaweed_s3_region` | `us-east-1` | S3 request-signing region |
| `seaweed_credential_path_prefix` | `managed/object_store` | External secret-store path prefix for bucket credentials |

The cluster's configured Vault backend must be writable. Astrolift stores each
access-key pair as a multi-key bundle there after the SeaweedFS Operator has
generated it in its owned Kubernetes Secret. The operator is the sole
credential issuer, so concurrent retries cannot leave Kubernetes and Vault
with different keys. Workloads receive field-selected Vault references;
plaintext is resolved only during runtime secret materialization.

Before applying any manifests, provisioning verifies the shared `Seaweed`
object and the `Bucket`, `S3Identity`, `S3Credentials`, `S3Policy`, and
`S3PolicyBinding` CRDs. A missing or older operator fails closed. Readiness is
withheld until every resource is ready and the generated credential pair is
synchronized into Vault. The default teardown retains the Bucket CR and data,
removes workload ownership and public access, and revokes IAM credentials.
Destructive teardown changes the reclaim policy to `Delete`; active Object
Lock retention still blocks deletion even when force-destroy is requested.

The archived community MinIO Operator is intentionally not an executable
Astrolift variant. Commercial MinIO AIStor and adoption of an existing
S3-compatible endpoint remain visible planned variants in the resource
catalogue.

---

## Decommission a cluster

Decommissioning removes the cluster from Astrolift's management. By
default it does not delete the Kubernetes cluster itself.

### Pre-decommission checklist

1. Identify all apps deployed to the cluster; `astro app list` prints a
   cluster column:
   ```
   astro app list
   ```
2. Migrate each app to another cluster or remove it. The workflow's first
   activity, `ensure_cluster_drained`, refuses while any active
   `AppEnvironment` is bound to the cluster. Remove cluster-owned models
   as well.
3. Verify no active deploys are in progress (status `deploying` or
   `redeploying`).

### Decommission steps

1. Go to **Cluster detail → Settings → Danger zone**.
2. Click **Decommission** (platform retirement only) or **Decommission
   and delete the cluster** (also runs the provider's `teardown_cluster`,
   which can permanently delete EKS / GKE / AKS infrastructure).
3. Confirm in the dialog. Acceptance means the request was queued, not
   that retirement completed; follow the lifecycle badge.

- **Signature:** a refused decommission lands the row in `lifecycle: error`
  with `lastManagementError` reading `decommission refused: cluster '<slug>'
  has <n> active app environment(s) bound — migrate them to a different
  cluster or delete them first` or `decommission refused: Remove all shared
  model deployments before retiring this cluster.`; a failure after the
  drain check reads `remove_platform_rbac failed: <exc>` or
  `teardown_cluster_infra failed: <exc>`.
- **Fix:** migrate or delete the bound environments and cluster-owned
  models, then click **Decommission** again; for a teardown failure fix the
  cloud-side cause and retry.
- **Rollback:** a refused decommission changes nothing on the cluster
  (`error` is re-entrant, **Retry bring into management** returns it to
  `managed`); once the workflow has marked the row `decommissioning` there
  is no path back (`Retiring clusters cannot return to management.`), so
  re-register the cluster under a new slug if it must be managed again.

`DecommissionClusterWorkflow-<cluster-guid>` marks the row
`decommissioning`, deletes the `astrolift-system` namespace (which
cascades the platform RBAC bundle), revokes the Zentinelle gateway, reaps
per-app cloud satellites (DNS records, ACM certs, ECR repos, IRSA roles;
best-effort, failures reported in the workflow result), optionally tears
down the cloud cluster, then marks the row `decommissioned`. The row is
kept for audit and leaves the active-cluster picker; it is not deleted.
Kubernetes-side resources created by Astrolift in app namespaces
(Deployments, Services, Ingresses) are left in place; you are responsible
for cleaning them up if needed. A failure lands the row in `error`;
inspect it with:

```bash
python manage.py temporal_ops describe DecommissionClusterWorkflow-<cluster-guid>
```

---

## Troubleshooting

**Cluster shows Degraded or Offline after network maintenance.**

- **Signature:** `astroliftCluster(slug:)` reports `heartbeatStatus`
  `degraded` (one missed interval) or `offline` (three missed intervals,
  default 30 s each) and `lastHeartbeatAt` stops advancing.
- **Fix:** the badge follows the keep-alive agent's heartbeat. Wait two
  intervals; if it stays Degraded or Offline, check the agent pods in
  `astrolift-system` and that the kubeconfig is still valid:

  ```bash
  kubectl --kubeconfig <path> get nodes
  kubectl --kubeconfig <path> -n astrolift-system get deploy,pods
  ```

  If the API endpoint changed (e.g. load balancer IP rotated), update it
  or re-run `register_tenant_cluster` as described under Rotate cluster
  credentials:

  ```graphql
  mutation {
    updateTenantCluster(input: {id: "<cluster-guid>", endpoint: "<new-endpoint>"}) {
      ok
      errors { code field message }
      data { id endpoint lifecycle }
    }
  }
  ```
- **Rollback:** run `updateTenantCluster` again with the previous
  `endpoint`.

**Preflight shows cert-manager missing but it is installed.**

- **Signature:** **Last management diagnostic** / `lastManagementError`
  names cert-manager as missing while `kubectl get pods -n cert-manager`
  shows it running.
- **Fix:** Astrolift reports cert-manager installed when the
  `certificates.cert-manager.io` CRD exists; pods in the `cert-manager` or
  `astrolift-system` namespace only supply the version. Confirm the CRD
  exists:

  ```bash
  kubectl get crds | grep cert-manager.io
  ```

  If the CRD is absent, install cert-manager (or select it in the
  Bootstrap recipe) and click **Re-run preflight**.
- **Rollback:** no rollback needed.

**Preflight shows metrics-server missing but `kubectl top nodes` works.**

- **Signature:** the diagnostic names metrics-server as missing while
  `kubectl top nodes` answers.
- **Fix:** Astrolift detects metrics-server from pods in `kube-system` (or
  `astrolift-system`) whose name contains `metrics-server` or that carry
  the `k8s-app=metrics-server` label:

  ```bash
  kubectl get pods -n kube-system -l k8s-app=metrics-server
  ```

  If metrics-server runs in another namespace or under another label, the
  probe does not see it; please file an issue.
- **Rollback:** no rollback needed.

---

## Verify

```bash
astro api graphql -q 'query { astroliftCluster(slug: "<slug>") { id lifecycle heartbeatStatus lastHeartbeatAt lastManagementError } }'
python manage.py temporal_ops describe BringClusterIntoManagement-<cluster-guid>
```

After bring-into-management the cluster reports `lifecycle: managed`,
`heartbeatStatus: connected` once the agent is installed and an empty
`lastManagementError`, and the workflow is completed; after decommission it
reports `lifecycle: decommissioned`.

## Related

- Cluster prerequisites: console page `/documentation/cluster-prerequisites`
- [Multi-cloud topology spec](../specs/multi-cloud-topology.md)
- [Deploy runbook](deploy.md)
