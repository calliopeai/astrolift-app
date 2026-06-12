# Runbook: Deploy workflow

Standard deploy and rollback procedures for Astrolift-managed apps.

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
astro deploy --app <slug> --cluster <cluster-slug>
```

Add `--wait` to block until the deploy reaches Running or Failed and
exit with code 1 on failure.

### Trigger via the API

```graphql
mutation {
  deployApp(appSlug: "<slug>", clusterSlug: "<cluster>") {
    ok
    deployment {
      id
      status
    }
    errors { field messages }
  }
}
```

---

## Monitor a deploy

The deploy detail page (reachable from **Deployments → [id]**) shows:

- **Status**: Queued → Building → Deploying → Running | Failed
- **Log stream**: real-time stdout/stderr from the build and deploy
  phases. Errors surface here before they appear as cluster events.
- **Cluster events**: Kubernetes events for the workload (ImagePullBackOff,
  OOMKilled, CrashLoopBackOff). Available once the deploy phase starts.

From the CLI:

```bash
astro deployments list --app <slug> --limit 10
astro deployments logs <deployment-id>
```

---

## Roll back to a previous deploy

Rollback re-applies the manifest and image tag of an earlier successful
deploy. It does not revert application data or environment variable
changes made after that deploy.

### From the UI

1. Open **App detail → Deployments**.
2. Find the deploy you want to restore. Click the row.
3. Click **Roll back to this deploy**. Confirm.

Astrolift creates a new deploy pointing at the historical manifest
snapshot and image digest. The rollback deploy appears in the list
with a **Rollback** badge.

### From the CLI

```bash
astro rollback --app <slug> --deployment <deployment-id>
```

### Estimated time

A rollback that reuses a cached image layer completes in under 60 seconds.
If the cluster has evicted the image layers, it adds a pull duration
(typically 30-90 seconds for a multi-hundred-MB image).

---

## Failed deploy: immediate remediation

If a deploy ends in **Failed** status:

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
   - If memory usage grows over time, the application has a leak — this
     is a bug in the application, not the platform.

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

## Related

- [Cluster management runbook](cluster-management.md)
- [Incident response runbook](incident-response.md)
- [Quickstart guide](../quickstart.md)
