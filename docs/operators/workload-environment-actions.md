# Review the environment before restart or scale

Public `astroliftServerInfo` discovery advertises `workloads.environment_targets`
when this additive contract is installed. That capability describes available
API fields; present app permissions and action preconditions still decide each
request. It does not establish a client's confirmation UI or native consent.

Preconditions: `app.read` on the workload's app for the review query and
`app.deploy` at the app scope for the mutation (`backend/core/permissions.py`);
run both through `astro api graphql` with a session or bearer token, or use the
Primary environment controls at `/apps/<app-slug>/workloads/<workload-slug>`;
the environment's cluster must be live. Placeholders: `<app-slug>` is `slug`
from `astro app list`; `<workload GUID>` is `id` from
`astroliftWorkloadsPage(appSlug: "<app-slug>")`; `<environment GUID>` is `id`
from `astroliftEnvironments(appSlug: "<app-slug>")`; `<cluster GUID>` and
`<reviewed namespace>` are `clusterId` and `namespace` as the review query
returns them, and the version numbers are its `workloadVersion`,
`environmentVersion`, `clusterVersion` and `appVersion`.

Restart and scale can target an explicit environment GUID. First read
`astroliftWorkloadActionTarget(workloadId: ..., environmentId: ...)`. It returns
immutable workload, app, environment and cluster GUIDs, their current versions,
the recorded namespace and advisory `viewerCan.restart` / `viewerCan.scale`
decisions. The read requires current `app.read` on the workload's app. An
unavailable, foreign or deleted mapping returns no target. Refresh and review
again when a target changes; environment names are display labels.

```graphql
query ReviewWorkload($workload: GUID!, $environment: GUID!) {
  astroliftWorkloadActionTarget(workloadId: $workload, environmentId: $environment) {
    workloadId workloadVersion appId appVersion
    environmentId environmentName environmentVersion
    clusterId clusterVersion namespace
    viewerCan { restart { allowed code reason } scale { allowed code reason } }
  }
}

mutation ScaleReviewedWorkload($input: ScaleWorkloadInput!, $version: Int!) {
  scaleAstroliftWorkload(input: $input, ifMatchVersion: $version) {
    ok errors { code message currentVersion requestedVersion }
    data {
      operationId accepted completed workloadVersion desiredReplicas readyReplicas
      target { workloadId appId environmentId clusterId namespace }
    }
  }
}
```

Send the read-back values without guessing:

```json
{
  "version": 7,
  "input": {
    "workloadId": "<workload GUID>",
    "environmentId": "<environment GUID>",
    "replicas": 2,
    "ifMatchEnvironmentVersion": 4,
    "expectedClusterId": "<cluster GUID>",
    "ifMatchClusterVersion": 3,
    "ifMatchAppVersion": 8,
    "expectedNamespace": "<reviewed namespace>"
  }
}
```

With each operation saved in its own file:

```sh
astro api graphql --file review.graphql --var workload=<workload GUID> --var environment=<environment GUID>
astro api graphql --file scale.graphql --vars-file scale.json
```

`restartAstroliftWorkload` uses the same target fields in `RestartWorkloadInput`,
without `replicas`. Explicit targets require every listed precondition, including
`ifMatchVersion`. Incomplete input returns `VALIDATION`; changed versions return
`VERSION_MISMATCH`; unavailable or changed cluster/namespace identity returns
`PRECONDITION`. No explicit-target failure falls back to a primary environment.

The backend locks the current app, environment, cluster and workload rows, checks
the complete review, and rechecks current `app.deploy` at the app scope using the
selected environment and cluster region. Current token validity and refreshed
scope ceilings are checked before dispatch. Existing browser freshness,
attestation and ABAC rules still apply. Advisory reads never authorize a write.
Selected-environment replica limits apply: zero through the smaller of 20 and a
positive environment `deploy_config.max_replicas`.

Success means the cluster accepted a patch. `accepted` is true, `completed` is
null, and `operationId` identifies the request's audit metadata. The response
includes the exact reviewed target and resulting workload version. Read-back
replicas or observed revision do not prove that a rollout completed. A network
error can leave the Kubernetes effect unknown; PostgreSQL rollback cannot undo
a provider patch. Inspect live state before deciding whether to repeat a write.
Deployment names remain Kubernetes names: this does not promise an atomic
Deployment UID precondition against external delete/recreate operations.

For compatibility, existing web and CLI callers that omit `environmentId`
retain the first nondeleted environment ordered by database ID. If that first
live mapping is inactive or otherwise invalid, review and mutation refuse it;
neither path silently selects a later live row. An explicit deleted environment
GUID is always refused without sibling fallback. They can continue
using optional `ifMatchVersion`. `AstroliftWorkload.primaryActionTarget` describes
only that compatibility target. Clients that display an explicit environment,
including native clients, must use its exact review query and all preconditions;
the primary workload `viewerCan` decision applies only to the primary target.
This backend contract does not certify a native client's confirmation UI.

The native Kubernetes driver supports Deployment merge patches and constructs
separate Kubernetes client configurations for each target. Two environments on
the same cluster use their separate recorded namespaces; two different clusters
retain separate kubeconfig identities even during concurrent connections. No
production resources are needed to verify this contract: the opt-in tests use
two task-owned local Kind clusters and PostgreSQL, with real provider calls.

## Verify

```sh
astro api graphql --file review.graphql --var workload=<workload GUID> --var environment=<environment GUID>
astro app pods <app-slug>
```

The re-read `workloadVersion` must equal the `workloadVersion` the mutation
returned, and the pod list must show the workload's pods reaching the requested
replica count with `STATUS` Running; until then `accepted: true` only means the
cluster took the patch.
