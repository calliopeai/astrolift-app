# Pipeline cluster routing and Knative function scaling

Two runtime fixes from #2554 (2026-10-26) that change what an operator sees on
a dedicated install: pipelines may now run on the shared platform cluster, and
`kind = "function"` workloads render annotations Knative accepts.

Preconditions: a pipeline with at least one job on an app in the organization
(`astro pipeline list`), an active managed cluster that is the organization's
or shared (`astro api graphql -q 'query { astroliftClustersPage { items { id slug isActive } } }'`), and for functions a cluster with Knative
Serving installed. Placeholders: `<run-id>` is from `astro pipeline list` or
`astro pipeline run`; `<slug>` is a cluster slug.

## Where a pipeline job runs

`backend/astrolift_pipelines/dispatch_router.py` picks a `TenantCluster` for a
pipeline job from its `runs_on` labels; the Temporal activity in
`backend/astrolift_workflows/activities/pipeline_job_spawn.py` calls it with
`astrolift/default` when the job sets nothing.

Rules:

- `self-hosted` or `macos` in `runs_on` forces the runner path; the K8s spawn
  refuses with `job requires a self-hosted runner`.
- `astrolift` means K8s only.
- `linux`, `windows`, `macos` match the cluster's `node_os`; `amd64`, `arm64`
  match `node_archs`; any other label must be present in `node_labels`.
- Candidate clusters are active and managed, not deleted, and belong to the
  job's organization **or are shared** (`organization IS NULL`). The
  organization's own clusters sort first, then shared ones, oldest first. Before
  #2554 shared clusters were excluded, so a dedicated install whose only
  cluster was the installer's shared one could never schedule a native
  pipeline. Clusters of another organization are never candidates.

When nothing matches, the run fails with
`organization '<slug>' has no cluster matching '<labels>'` plus a reason. One
reason to read carefully: `N cluster(s) have no recorded node architecture`.
`node_archs` is filled by `ReconcileClusterCapabilitiesWorkflow` every 10
minutes from the cluster's nodes; a cluster brought into management moments
ago, or one whose nodes the platform's RBAC cannot list, matches no
architecture selector on purpose.

## Knative functions

A `[[workloads]]` entry with `kind = "function"` renders a Knative `Service`
(`backend/astrolift_manifest/render.py`, `_render_function`). The
tenant cluster needs Knative Serving; the k8s-native preflight requires the
`services.serving.knative.dev` CRD and prints the `knative-operator` Helm
install when it is missing (`backend/providers/k8s_native/preflight.py`).

Manifest keys and defaults (`backend/astrolift_manifest/parser.py`):

| Key | Default | Rendered as |
|---|---|---|
| `min_scale` | `0` (scale to zero) | `autoscaling.knative.dev/minScale` on the revision template |
| `max_scale` | `10` | `autoscaling.knative.dev/maxScale` on the revision template |
| `concurrency` | `1` | `containerConcurrency` |
| `timeout_seconds` | `300` | `timeoutSeconds` |

Since #2554 the scaling annotations are emitted only on
`spec.template.metadata.annotations`. They used to be duplicated on the
Service's own metadata, and Knative rejected the Service for it; a function
deploy that failed at `apply_manifests` with a Knative admission error on a
build before 2026-10-26 was this. Redeploying
on the current backend is the fix; the manifest needs no change.

## Verify

```bash
# The job's pod is on the expected cluster; a shared fallback shows the shared cluster's slug
astro pipeline logs <run-id>
# The cluster records an architecture, so arch selectors can match it (node_archs is not in GraphQL)
python manage.py shell -c "from astrolift_clusters.models import TenantCluster; print(list(TenantCluster.objects.values_list('slug','node_os','node_archs')))"
# A function deploy produced a Ready Knative Service with the annotations on the revision template only
kubectl -n <app namespace> get ksvc <workload> -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}{.spec.template.metadata.annotations}'
```

## Related

- [workload-and-node-autoscaling.md](workload-and-node-autoscaling.md)
- [pipeline-creation-access.md](pipeline-creation-access.md)
- [shared-environment-namespaces.md](shared-environment-namespaces.md)
