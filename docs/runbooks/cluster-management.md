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
