# Runbook: Cluster management

Procedures for bringing a Kubernetes cluster into Astrolift management,
ongoing maintenance, and controlled decommission.

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

See [cluster-prerequisites.md](../operators/cluster-prerequisites.md) for
install instructions if any are missing.

### Registration steps

1. **Prepare the kubeconfig.** Export a kubeconfig scoped to the
   permissions Astrolift needs. The control plane needs permission to:
   - Create and manage `Namespace`, `Deployment`, `Service`,
     `Ingress`, `Certificate` resources in app namespaces.
   - Read `Node`, `Pod`, `Event` resources cluster-wide (for the
     cluster health dashboard).

   For a minimum-permission kubeconfig, use the `astro cluster kubeconfig`
   helper or create a `ClusterRole` and `ServiceAccount` manually.

2. **Register in Astrolift.**
   - Go to **Clusters → Register cluster**.
   - Fill in the name, provider, region, and paste the kubeconfig.
   - Click **Register**. Astrolift runs a preflight check automatically.

3. **Review preflight results.** The cluster detail page shows a
   bootstrap card listing required and optional components. Green
   means detected; amber means recommended but missing; red means
   required and missing. Missing required components block app deploys
   to that cluster until they are installed.

4. **Label the cluster.** Add labels to classify the cluster for
   placement policies. Standard label keys:
   - `env`: `production`, `staging`, `development`
   - `region`: matches the cloud provider's region slug (e.g. `us-west-2`)
   - `provider`: `aws`, `gcp`, `azure`, `k8s_native`

   Set labels from **Cluster detail → Settings → Labels**.

### Verification

After registration:

- The cluster status shows **Healthy** (green).
- The cluster appears as a deployment target in the app deploy wizard.
- The cluster activity feed shows the first reconciliation events.

---

## Cluster health monitoring

The cluster detail page provides:

- **Status indicators**: real-time checks for API server reachability,
  cert-manager, ingress controller, and metrics-server.
- **Node list**: CPU and memory utilization per node.
- **Recent activity**: deploy events, scaling events, OOMKills, restarts.
- **Alerts**: any active Prometheus alert rules targeting this cluster
  (if in-cluster Prometheus is configured).

Astrolift polls the cluster every 60 seconds and marks it **Degraded**
if the API server stops responding for more than 2 consecutive polls.
**Unreachable** if the cluster does not respond for 10 minutes.

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

1. Generate a new kubeconfig / token using the same steps as initial
   registration.
2. Go to **Cluster detail → Settings → Credentials**.
3. Click **Rotate credentials** and paste the new kubeconfig.
4. Click **Save**. Astrolift validates reachability immediately; if
   validation fails, the old credentials remain active.

Do not revoke the old credentials until Astrolift confirms the new
ones work.

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

Decommissioning removes the cluster from Astrolift's management. It
does not delete the Kubernetes cluster itself.

### Pre-decommission checklist

1. Identify all apps deployed to the cluster:
   ```
   astro apps list --cluster <cluster-slug>
   ```
2. Migrate each app to another cluster or remove it. Astrolift blocks
   decommission if any apps are actively deployed to the cluster.
3. Verify no active deploys are in progress (status **Running** or
   **Deploying**).

### Decommission steps

1. Go to **Cluster detail → Settings → Danger zone**.
2. Click **Decommission cluster**.
3. Type the cluster slug to confirm.
4. Click **Confirm decommission**.

Astrolift removes the cluster record and all associated Astrolift
resources from the database. The kubeconfig is purged from the secrets
store. Kubernetes-side resources created by Astrolift (Deployments,
Services, Ingresses in app namespaces) are left in place — you are
responsible for cleaning them up if needed.

---

## Troubleshooting

**Cluster shows Degraded after network maintenance.**
Wait 2-3 minutes for the health check backoff to clear. If it stays
Degraded, verify the kubeconfig is still valid:

```bash
kubectl --kubeconfig <path> get nodes
```

If the API endpoint changed (e.g. load balancer IP rotated), rotate
credentials with the updated kubeconfig.

**Preflight shows cert-manager missing but it is installed.**
Astrolift checks for `cert-manager.io/v1` CRD availability and for at
least one Ready pod in any namespace named `cert-manager`. If your
install is in a non-standard namespace, the pod check may not find it —
confirm CRDs exist:

```bash
kubectl get crds | grep cert-manager.io
```

If CRDs are present but pods are in a custom namespace, file a support
request to configure the namespace override.

**Preflight shows metrics-server missing but `kubectl top nodes` works.**
Astrolift checks the `v1beta1.metrics.k8s.io` APIService:

```bash
kubectl get apiservice v1beta1.metrics.k8s.io
```

If it shows `False`, the metrics-server pods are not healthy. If it
shows `True`, this is a platform bug — please file an issue.

---

## Related

- [Cluster prerequisites](../operators/cluster-prerequisites.md)
- [Multi-cloud topology spec](../specs/multi-cloud-topology.md)
- [Deploy runbook](deploy.md)
