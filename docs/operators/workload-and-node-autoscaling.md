# Workload and node autoscaling

Treat replica count, CPU node capacity and GPU node capacity as separate policies.

| Layer                 | Owner and signal                                   | Astrolift configuration                                                                                            |
| --------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| CPU workload replicas | HPA, measured CPU as a percentage of requested CPU | Set both `hpa_min` and `hpa_max`, with an explicit `hpa_target_cpu_pct`.                                           |
| CPU node group        | Cluster Autoscaler, pods that cannot be scheduled  | Operator installs a compatible controller and grants discovery and scale permissions for only the intended groups. |
| Always-on GPU pool    | Operator, explicit availability and cost policy    | Keep a minimum node, omit autoscaler discovery tags, and taint the pool.                                           |

## Configure a workload HPA

```toml
[[workloads]]
name = "web"
kind = "deployment"
cpu_request = "250m"
memory_request = "256Mi"
hpa_min = 2
hpa_max = 6
hpa_target_cpu_pct = 75

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080
```

Astrolift renders an `autoscaling/v2` HPA targeting this Deployment. With both
bounds configured, the Deployment omits `spec.replicas`: the HPA owns its scale
subresource across redeploys. This also applies to service-family agents and
Temporal workflow workers. Without both bounds, deployments retain their
explicit `replicas` value; preview cost containment disables the HPA and keeps
one replica. Knative functions retain their own autoscaling behavior.

The CPU target uses the requested CPU, not the node's total CPU. Every container
that contributes to the resource metric needs a request, including sidecars.
Verify Metrics Server and a working `metrics.k8s.io` API before enabling the
HPA. Both bounds should be valid positive integers, with min no greater than max;
the existing manifest parser does not comprehensively validate those inputs.
Kubernetes rejects an invalid HPA. The default CPU target remains 80%; the
example explicitly chooses 75%.

For an existing deployment, establish a healthy HPA and verify that its `/scale`
update owns `spec.replicas` before the first deploy that releases Astrolift's
ownership. Kubernetes can otherwise default the field to one during transfer.
Watch managed fields, desired replicas and availability during this transition.
An externally installed HPA requires matching manifest bounds before Astrolift
can release ownership; otherwise its manifest still expresses fixed replicas.
Manual scaling is transient while an HPA remains active. To return to fixed
capacity, remove both bounds and explicitly remove the old HPA; omission from
the rendered manifest does not itself prune Kubernetes objects.

## Demand-driven CPU nodes

Use Cluster Autoscaler for scheduling demand. Its scale-down utilization
threshold considers CPU and memory **requests** relative to allocatable capacity;
50% for ten minutes makes a node a candidate, subject to rescheduling, minimum
size, eviction and PodDisruptionBudget constraints. Low measured CPU does not
imply that all pods fit on fewer nodes. A raw ASG CPU target-tracking policy does
not provide this Kubernetes scheduling and drain policy.

For EKS, the reusable installation split is:

1. Terraform owns the selected node-group bounds, its ASG discovery tags, and a
   least-privilege IRSA role restricted to the controller's exact service account
   and eligible groups. Inspect the managed ASG tags, not only EKS group tags.
   Ensure subsequent Terraform runs do not reset controller-owned desired size.
2. A reviewed Flux HelmRelease owns the controller deployment and its service
   account annotation using the role ARN output. Match the controller release to
   the cluster's Kubernetes version. Install it after those prerequisites exist.
3. Verify discovery, an unschedulable test workload, bounded scale-out and PDB
   aware scale-in in a disposable environment before enabling production use.

Choose node-group bounds and zone distribution per installation. A multi-zone
group does not guarantee exactly one node per zone. The current generic Opscode
EKS roots provide node groups but do not install this autoscaler or its dedicated
IRSA/discovery wiring. This runbook does not claim that deployment is complete.

## GPU availability policy

For an always-on model server, keep a separate on-demand GPU node group with an
explicit nonzero minimum and no autoscaler discovery tags. Apply
`nvidia.com/gpu=present:NoSchedule` so ordinary platform and tenant pods cannot
consume it; Astrolift GPU workloads already tolerate that taint. See
[GPU workloads](gpu-workloads.md) for device-plugin and scheduling requirements.

If multiple instance types are offered, keep compatible GPU count, memory and
pod capacity; fallbacks are not an ordered guarantee that the first type wins.
Do not apply a generic CPU HPA to a model server by default. Future demand-based
GPU scaling needs an explicit model concurrency/queue metric, admission policy,
cold-start budget and node-pool policy.

## Verification and references

The local Kubernetes regression replays a legacy Astrolift SSA owning replicas,
a simulated HPA `/scale` update from three to five, and an image redeploy with
replicas omitted. It checks that five survive, Astrolift releases the field,
an unchanged reapply stays at five, and an explicit fixed-size transition works.
It proves apiserver field ownership, not live Metrics Server, HPA load behavior,
AWS node scaling or a production cutover. Run against a disposable local cluster:

```sh
ASTROLIFT_TEST_KUBECONFIG=/path/to/local-kind.yaml \
  python -m pytest astrolift_manifest/tests/test_hpa_scale_ownership_integration.py
```

Sources: [Kubernetes HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/),
[Cluster Autoscaler FAQ](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md),
[EKS autoscaling guidance](https://docs.aws.amazon.com/eks/latest/best-practices/cas.html).
