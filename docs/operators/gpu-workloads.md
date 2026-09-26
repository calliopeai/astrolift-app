# GPU workloads

Astrolift schedules GPU work (model hosting, inference APIs, GPU agents) on GPU nodes and keeps everything else off them. This page covers what the platform does and what an operator sets up per cluster.

## What an app declares

A workload asks for GPUs in `astrolift.toml`:

```toml
[[workloads]]
name = "llm"
kind = "deployment"
gpu = 1                    # whole GPUs, or MIG slices when mig_profile is set
gpu_type = "nvidia-l4"     # optional: GPU product (GFD label) or GKE accelerator
mig_profile = "3g.47gb"    # optional: request MIG slices instead of whole GPUs
```

The platform then does four things:

1. **Requests the GPU on the primary container only.** It sets `nvidia.com/gpu`, or `nvidia.com/mig-<profile>` for a MIG slice. Sidecars never hold a device.
2. **Pins the node type,** when `gpu_type` is set. It adds required node affinity on `nvidia.com/gpu.product` or `cloud.google.com/gke-accelerator`.
3. **Tolerates the GPU-pool taints** `nvidia.com/gpu` and `astrolift.io/gpu`, both `NoSchedule`. Only workloads that request a GPU get these tolerations.
4. **Refuses a deploy up front** when no single node of the cluster can hold the request, based on the last capability probe. The operator sees a clear message instead of a pod stuck in Pending.

## Per-cluster setup

### Keep other workloads off GPU nodes

Taint every GPU node pool `NoSchedule`. Pods that don't tolerate the taint, including platform pods, then never land on GPU nodes.

| Cloud | How |
|---|---|
| GKE | GKE taints GPU node pools with `nvidia.com/gpu=present:NoSchedule` itself. Nothing to do. |
| EKS | Add the taint to the GPU managed node group or Karpenter NodePool: `nvidia.com/gpu=present:NoSchedule`. |
| AKS | `az aks nodepool add ... --node-taints nvidia.com/gpu=present:NoSchedule` |
| On-prem | `kubectl taint nodes <node> astrolift.io/gpu=true:NoSchedule` |

### Expose the GPUs to Kubernetes

Install the NVIDIA GPU Operator, or at least the NVIDIA device plugin, so that nodes report allocatable `nvidia.com/gpu`. The GPU Operator also brings:

- GPU feature discovery labels (`nvidia.com/gpu.product`, `nvidia.com/gpu.memory`);
- the MIG manager, for MIG profiles set through the `nvidia.com/mig.config` node label;
- the DCGM exporter, for GPU metrics.

The cluster's capability probe records GPU nodes, GPU and MIG totals, and whether the device plugin, GPU operator and DCGM exporter are running.

### Clusters that create GPU nodes on demand

On GKE Autopilot, or with Karpenter GPU NodePools, a cluster may have no GPU nodes until a pod asks for one. Set `gpu_autoprovision: true` in the cluster's provider config. The deploy check then accepts the request instead of refusing it for zero GPUs.

## GPU quota per organization

An org-scope quota on the `gpu` resource caps the GPUs an organization's workloads hold at once. A deploy is refused when its GPUs plus the org's running deploys and vLLM models would pass the hard limit. Each workload counts at its ceiling: `gpu` times its HPA `hpa_max` (or Knative `max_scale`), else `replicas`. MIG slices count as one each. Redeploying an environment doesn't count what it replaces. With no `gpu` quota there is no limit.
