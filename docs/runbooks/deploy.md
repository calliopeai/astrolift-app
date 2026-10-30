# Runbook: Deploy workflow

Standard deploy and rollback procedures for Astrolift-managed apps.

Preconditions: an `astro` login on the install (`astro auth status`) with
`app.deploy` (deploy) or `app.rollback` (rollback) on the app; for the
Temporal steps, a shell in the backend container (ECS `exec` into the
webservice or worker task, or `kubectl exec` into the api pod) where
`python manage.py` runs. Placeholders: `<slug>` is the app slug from
`astro app list`; `<deployment-id>` is the GUID printed by `astro app deploy`
or `astro ci status`, or `id` from `astroliftDeploymentsPage`; `<guid>` in a
workflow id is that deployment id.

---

## Deploy an app

### Trigger from the UI

1. Open **Apps** and click the target app.
2. Click **Deploy** (top-right).
3. Select the target cluster. Confirm.
4. Watch the deploy log stream on the deploy detail page.
5. When status shows **Running**, verify via the **Open** link.

### Trigger from a git push (source-connected apps)

Push to the branch configured as the deploy branch:

```
git push origin main
```

Astrolift receives the webhook, creates a build job, and starts a
deploy automatically. The deploy appears in the **Deployments** list
within 5-10 seconds of the push.

### Trigger via the CLI

```bash
astro app deploy <slug> --env production --image-tag v1.2.3 --wait
# Platform builds: resolve the configured deploy branch and build its commit.
astro app deploy <slug> --env production --wait
# Override the source with a branch, tag, or commit.
astro app deploy <slug> --env production --ref release/v2 --wait
```

Add `--wait` to block until the deploy reaches Running or Failed and
exit with code 1 on failure.

### Trigger via the API

```graphql
mutation {
  startDeployment(input: {
    appSlug: "<slug>"
    environmentName: "production"
    sourceRef: "release/v2"
  }) {
    ok
    data {
      id
      status
      imageTag
      commitSha
      branch
    }
    errors { code field message }
  }
}
```

The app's build mode controls `imageTag`:

| Build mode | Image tag behavior |
|---|---|
| `ci_pushed` | Required; use a tag already published by CI. |
| `platform_build` | Optional. Without a tag, the API resolves the source to a full commit SHA and uses that SHA as the image tag. Requires an active source connection and a build strategy. |
| `none` | Optional when container images are declared with `image_ref` in the saved manifest. No image override is supplied. |

`sourceRef` resolves a branch, tag, or commit through the organization's stored
source connection. Omit it to use the configured deploy branch for an untagged
platform build. Existing `commitSha` and `branch` metadata still work; do not
combine `sourceRef` with `commitSha`. An explicit image tag remains unchanged.

Resolution happens before creating a deployment or superseding in-flight work.
Approval retains the recorded commit even if the branch moves while waiting.
Both immediate and approved workflows build that commit, and the pre-deploy
manifest resync requests it. Resync retains its existing draft-conflict and
fetch-failure behavior: it reports the outcome and may use the saved manifest.
This pins the source revision, not every mutable app setting.

---

## Monitor a deploy

The deploy detail page (reachable from **Deployments → [id]**) shows:

- **Status**: `pending_approval` (when the environment requires
  approvals) → `pending` → `deploying` → `running` | `failed`. A `running`
  deploy becomes `superseded` when the next deploy goes live, `rolled_back`
  when it is rolled back, or `redeploying` during a redeploy. `failed`,
  `superseded` and `rolled_back` are terminal; nothing moves a row out of
  them (`Deployment._TRANSITIONS` in
  `backend/astrolift_lifecycle/models/deployment.py`).
- **Reason**: `statusReason` / `abortedReason` on the deployment carry why a
  deploy failed or was aborted.
- **Log stream**: real-time stdout/stderr from the build and deploy
  phases. Errors surface here before they appear as cluster events.
- **Cluster events**: Kubernetes events for the workload (ImagePullBackOff,
  OOMKilled, CrashLoopBackOff). Available once the deploy phase starts.

From the CLI:

```bash
astro ci status <deployment-id>
astro app logs --env production --since 1h --tail 200 -f
```

From GraphQL, `astroliftDeploymentsPage(appSlug:, environmentName:, statuses:)`
lists deployments and `astroliftDeployment(id:)` reads one.

---

## Roll back to a previous deploy

Rollback re-applies the image tag, digest and config snapshot of the
deploy that was live before the current one. It does not revert
application data or environment variable changes made after that deploy.

The target of `rollbackDeployment` is always the **current `running`**
deployment of the environment; the mutation refuses any other row
(`only running deployments are rollback targets`, `only the current running
deployment is a rollback target`). The deploy it rolls back to is the most
recent `superseded` deployment created before it; with none, the mutation
answers `no prior superseded deployment to roll back to`. On a production
environment the action-safety gate (#2202) is consulted first, as action
`rollback.production`.

What happens: the running row moves to `rolled_back`, a new row with
`triggerKind: rollback` is created in `pending` carrying the prior deploy's
image and snapshot (and its strategy), and `RollbackDeploymentWorkflow`
(workflow id `RollbackDeploymentWorkflow-<new deployment guid>`) runs the
ordinary apply path against it: `pre_flight`, `mark_deploying`,
`render_manifests`, `update_secrets`, `apply_manifests`, `wait_dns`,
`poll_rollout`, `health_check`, `mark_running`. Apps whose environment
requires a native identity origin (native GKE) roll back through the native
pipeline instead (`_start_native_rollback`, #2278).

### From the UI

1. Open **App detail → Deployments**, or **Deployments → [id]** for the
   running deployment.
2. Click **Roll back**. Confirm. A failed latest deploy also shows
   **Roll back** on the app overview when a good prior deploy exists.

The rollback deploy appears in the list with the `rollback` trigger kind.

### From the CLI

```bash
# Resolve the running deployment for --app / astrolift.toml and --env
astro app rollback --env production
# Or name the running deployment
astro app rollback <deployment-id> --yes
```

### Stuck rollback: `RollbackDeploymentWorkflow` retrying forever

`RollbackDeploymentWorkflow` runs its activities with Temporal's default
retry policy (unbounded attempts) and handles no `abort` signal; only
`DeployAppWorkflow` does. When the rollback's deployment row is already
terminal, in practice `failed` after an operator pressed **Abort**
(`abortDeployment` signals `abort`, the server accepts the signal, so the
workflow is not terminated, and the row is moved to `failed`),
`mark_deploying` or `mark_running` raises
`Deployment(<id>) cannot transition failed → running` on every attempt,
and the workflow retries it indefinitely, holding a worker slot.

Symptoms: a `RollbackDeploymentWorkflow-<guid>` execution that stays
`Running` long after its 15 minute activity timeouts; the Temporal UI shows
the same activity with a climbing attempt count and that `ValueError`.

Resolution:

```bash
# From the backend container
python manage.py temporal_ops list --status running | grep RollbackDeploymentWorkflow
python manage.py temporal_ops describe RollbackDeploymentWorkflow-<guid>
python manage.py temporal_ops terminate RollbackDeploymentWorkflow-<guid> \
  --reason "deployment row already failed; nothing left to mark running"
```

Terminating leaves the row as it is (`failed`, with its `abortedReason`). To
restore service, roll back again from the new running deployment if one
exists, or deploy a known-good image with `astro app deploy --image-tag`.
The row the rollback moved to `rolled_back` cannot be revived; it is
history. The workflow-level fix (a bounded retry policy and an `abort`
handler on the rollback workflow) is not shipped as of 2026-10-30.

### Estimated time

A rollback that reuses a cached image layer completes in under 60 seconds.
If the cluster has evicted the image layers, it adds a pull duration
(typically 30-90 seconds for a multi-hundred-MB image). Each rollback
activity has a 15 minute start-to-close timeout.

---

## Failed deploy: immediate remediation

If a deploy ends in `failed` status:

0. **Deploy pipeline disabled?** When every deploy, promote and rollback
   answers `deploy pipeline is disabled in this environment`, the Constance
   flag `DEPLOY_PIPELINE_ENABLED` is off. Flip it in `/app/admin/constance/`
   or seed it with the `FEATURE_DEPLOY_PIPELINE` env var on the webservice
   and worker.

1. **Read the log**: open the deploy detail page, scroll to the error.
   Most failures are one of: build error (Dockerfile), image pull error
   (bad credentials or private registry), or pod crash (application
   startup error).

2. **Build failures**: the log shows the Docker build output. Fix the
   Dockerfile, push a new commit, and a new deploy triggers
   automatically (or click Deploy manually).

3. **ImagePullBackOff**: the cluster cannot pull the image.
   - Check that the registry credentials secret is present in the
     target namespace.
   - Verify the image tag exists in the registry.
   - For private registries, confirm Astrolift has a registry
     connection under **Settings → Registries**.

4. **CrashLoopBackOff**: the container is starting and crashing.
   - The application logs (available via **App detail → Logs**) show
     the error before the crash.
   - Common causes: missing required environment variable, database
     not reachable at startup, port mismatch between app and manifest.

5. **OOMKilled**: the container exceeded its memory limit.
   - Raise the memory limit in **App detail → Resources** and redeploy.
   - If memory usage grows over time, the application has a leak; this
     is a bug in the application, not the platform.

6. **Dismiss a failed row**: `abortDeployment` on a `failed` deployment
   soft-deletes the record (no workflow is left to stop). On an in-flight
   row (`pending_approval`, `pending`, `deploying`, `redeploying`) it
   signals `abort` to `DeployAppWorkflow`, falls back to terminating the
   workflow when the signal cannot be delivered, and marks the row
   `failed`. See the stuck-rollback note above before aborting a rollback.

---

## Zero-downtime deploy considerations

Astrolift performs rolling updates by default: it brings up new pods
before terminating old ones, and only routes traffic to pods that pass
the readiness probe.

For zero-downtime, your app must:

- Define a readiness probe in `astrolift.toml` under `[health]`. A
  missing readiness probe means Kubernetes sends traffic to new pods
  the moment they start, before the application is ready.
- Handle `SIGTERM` gracefully. Kubernetes sends `SIGTERM` before
  killing the pod; your app should finish in-flight requests within the
  `terminationGracePeriodSeconds` window (default: 30s).

---

## Verify

```bash
# The environment has exactly one running deployment and it is the one you expect
astro ci status <deployment-id>
# Or, through GraphQL: statuses: ["running"] returns one row for the environment
astro api graphql --query 'query { astroliftDeploymentsPage(appSlug: "<slug>", environmentName: "production", statuses: ["running"]) { items { id status imageTag triggerKind } } }'
```

A rollback is complete when the new `triggerKind: rollback` row is `running`,
the previous row is `rolled_back`, and `temporal_ops list --status running`
no longer lists `RollbackDeploymentWorkflow-<guid>`.

## Related

- [Cluster management runbook](cluster-management.md)
- [Incident response runbook](incident-response.md)
- [Quickstart guide](../quickstart.md)
