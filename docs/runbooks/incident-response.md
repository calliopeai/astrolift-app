# Runbook: Incident response

Triage guide for the most common failure modes in Astrolift-managed
environments. Each section covers: what you observe, how to confirm,
and how to resolve.

Preconditions: `app.read` and `app.read_logs` on the app to read its
deployments, pods and logs, `app.deploy` to abort a deployment, `cluster.read`
to read a cluster's lifecycle and heartbeat, `scm.connect` to rotate a webhook
signing secret; the console (`/deployments/[id]`, `/apps/[slug]/logs`,
`/clusters/[slug]`, `/providers#source`, `/webhooks/[id]/deliveries/[deliveryId]`)
or an `astro` login (`astro auth status`); a kubeconfig for the app's cluster
for the `kubectl` steps; a shell in the backend container (ECS `exec` into the
webservice or worker task, or `kubectl exec` into the api pod) for
`python manage.py`; on an ECS install, the control plane's AWS account for
`aws ecs` and `aws logs`. Placeholders: `<app-namespace>` is
`<org-slug>-<app-slug>` unless pinned; `<pod-name>` from `astro app pods
<app-slug>` or `kubectl get pods`; `<workload-name>` is the `[[workloads]]`
name in `astrolift.toml`; `<workflow-id>`, including the `<app-guid>` and
`<env-guid>` in a `DeployAppWorkflow` id, from `python manage.py temporal_ops
list --status running`; `<astrolift-host>` is the API origin (`APP_BASE_URL`);
`<cluster-name>` and `<name>` are the ECS cluster and the opscode service
prefix; `<slug>` is the cluster slug and `<cluster-api-endpoint>` its
`endpoint`, both from `astroliftCluster(slug:)` or `astroliftClustersPage`;
`<path>` is the `--request-file` `astro operator cluster install-agent`
creates on its first run and re-reads on a retry, and `<uuid>` is the
installation id it prints (`Agent installation <id>: <status>`); `<connectionId>` is the
source connection on `/providers#source`; `<app_guid>` is `id` from
`astroliftApps`; `<org_slug>` from `astroliftOrganizations`;
`<opensearch-host>` is the endpoint of the app's attached `search` managed
service; `<image-reference>` is the pulled image from the pod events.

---

## App is not reachable (502 / 503 / connection refused)

**What you observe:** The app URL returns an error. The Astrolift UI
shows the app status as **Running** (deploy succeeded) but the
endpoint is not working.

**Confirm the pod is actually running** (the app namespace is
`<org-slug>-<app-slug>` unless the app or environment pinned its own
`k8s_namespace`; `astro app pods <app-slug>` lists pods without kubectl):

```bash
kubectl get pods -n <app-namespace>
```

Expected: at least one pod in `Running` state and `1/1` ready.

**Pod is Running but returning errors:**

- **Signature:** `kubectl get pods` shows `Running` and `1/1`, the URL still
  answers 502/503, and the previous container log ends in the application's
  own error.
- **Fix:** the application itself is crashing after startup. Check the
  application logs:

  ```bash
  kubectl logs -n <app-namespace> <pod-name> --previous
  ```

  Or from the Astrolift UI: **App detail → Logs**, or the CLI:

  ```bash
  astro app logs <workload-name> --follow
  ```

  Common causes: missing required env var, database connection refused
  at startup, port mismatch.
- **Rollback:** return the environment to the previous image while the fix
  is prepared:

  ```bash
  astro app rollback --env <env>
  ```

**Pod is in CrashLoopBackOff:**

- **Signature:** `kubectl get pods` shows STATUS `CrashLoopBackOff` with a
  climbing RESTARTS count.
- **Fix:** the container starts and crashes repeatedly. Check the log (above).
  The most recent logs before the crash are usually the first place to
  look. Once the root cause is fixed (env var, dependency, code),
  redeploy.
- **Rollback:** `astro app rollback --env <env>` as above.

**Pod is in Pending:**

- **Signature:** `kubectl get pods` shows STATUS `Pending`; the Events in
  `kubectl describe pod` name the cause.
- **Fix:** the pod cannot be scheduled. Check cluster events:

  ```bash
  kubectl describe pod <pod-name> -n <app-namespace>
  ```

  Common causes:
  - **Insufficient resources**: the cluster has no nodes with enough
    CPU or memory for the pod's resource request. Scale up the cluster
    or lower the resource request in the app's manifest.
  - **Node selector / toleration mismatch**: the manifest requests a
    node with a label that no current node has.
  - **PVC pending**: the pod mounts a PVC that cannot be provisioned
    (no default storage class, quota exceeded).
- **Rollback:** a manifest change is undone by restoring the previous
  values in `astrolift.toml`, `astro app manifest push` and redeploying;
  added nodes need no rollback.

**Ingress is not routing to the pod:**

- **Signature:** the pod is Running but the ingress returns 503, and the
  Service has no endpoints.
- **Fix:** check that the Kubernetes Service has endpoints:

  ```bash
  kubectl get endpoints <workload-name> -n <app-namespace>
  ```

  Astrolift names each Service after its workload. If the list is empty,
  the Service selector (`astrolift.dev/workload=<workload-name>`) does not
  match the pod labels, or the pod is not ready. Check readiness probe logs.
- **Rollback:** no rollback needed.

---

## Deploy is stuck (`deploying` for more than 20 minutes)

Deployment statuses are `pending_approval`, `pending`, `deploying`,
`running`, `redeploying`, `failed`, `superseded` and `rolled_back`. Each
rollout is one Temporal execution,
`DeployAppWorkflow-<app-guid>-<env-guid>`; a newer deploy to the same
(app, env) aborts the in-flight one. Rollout activities time out at
20 minutes, platform image builds at 30, and the rollout poll fails
terminally after its 600 s window, so a deploy sitting in `deploying`
past those bounds has lost its worker or is waiting on the cluster.

**Signature:** `astroliftDeployment(id:)` reports `status: deploying` with an
empty `abortedReason` past the 20 minute activity bound; `temporal_ops
describe` shows the `DeployAppWorkflow-<app-guid>-<env-guid>` execution
`Running` with an activity still scheduled (no worker picked it up) or
`poll_rollout` still pending.

**Fix:**

**Check the deploy log in the UI** for the last logged line, then the
workflow itself:

```bash
python manage.py temporal_ops list --status running
python manage.py temporal_ops describe DeployAppWorkflow-<app-guid>-<env-guid>
```

The Temporal UI (compose service `temporal-ui`, port 8233 locally) shows
the same history with the pending activity and its last failure.

**Check the Temporal worker:** the `<name>-worker` ECS service must be
running and polling the task queue; a workflow with no worker shows its
activity as scheduled but never started.

**Stop a stuck deploy:** abort it from the deployment row (**Abort**,
with a reason) or via the API; the workflow marks the deployment `failed`
with that reason:

```graphql
mutation {
  abortDeployment(input: {id: "<deployment-id>", reason: "<why>"}) {
    ok
    errors { code field message }
    data { id status abortedReason }
  }
}
```

As a last resort terminate the execution; the row then stays `deploying`
until the next deploy or rollback supersedes it:

```bash
python manage.py temporal_ops terminate <workflow-id> --reason "<why>"
```

Redeploy once the cause is fixed.

**Rollback:** an abort moves the row to `failed`, which is terminal; a
termination cannot be undone. Neither changes what is running on the
cluster. Redeploy, or `astro app rollback --env <env>` if the half-applied
rollout left the environment unhealthy.

**If the image pull is the bottleneck:**

Large images can take several minutes to pull on a cold cluster. Check:

```bash
kubectl describe pod <pod-name> -n <app-namespace>
# Look for: Pulling image "..." events
```

The deploy completes once the pull finishes. If the pull stalls for
more than 10 minutes, check registry connectivity from the cluster
node:

```bash
# On a node (via kubectl debug or SSH):
docker pull <image-reference>
# or
crictl pull <image-reference>
```

---

## Control plane is unreachable

**Symptoms:** the Astrolift frontend is down, or API requests return
errors or time out.

**Signature:** `curl -sf "https://<astrolift-host>/health/?format=json"`
returns non-200 and names a checker that is not `working`, or does not
answer at all; `aws ecs describe-services` shows `running` below `desired`
for `<name>-app` or `<name>-worker`.

**Fix (infrastructure team):**

1. Verify the Django API service is healthy (django-health-check,
   mounted at the project root, not under `/app/`):
   ```bash
   curl -sf "https://<astrolift-host>/health/?format=json" | jq .
   ```
   Expected: HTTP 200 with every checker (database, cache, storage)
   reporting `working`. A non-200 names the failing checker.

2. Check ECS service status (if running on ECS Fargate; the opscode
   module names the services `<name>-app` and `<name>-worker`):
   ```bash
   aws ecs describe-services \
     --cluster <cluster-name> \
     --services <name>-app <name>-worker \
     --query 'services[].{name:serviceName,status:status,running:runningCount,desired:desiredCount}'
   ```

3. Tail ECS logs:
   ```bash
   aws logs tail /aws/ecs/<name> --follow        # app
   aws logs tail /ecs/<name>-worker --follow     # Temporal worker
   ```

4. Check the database connection. If the API container cannot reach
   Postgres, it surfaces in the health check response or in the logs
   immediately on startup.

5. Check Redis. It is the Django cache (`DJANGO_CACHE_URL`) and fails
   the `/health/` cache checker when down. Background work runs on
   Temporal (`TEMPORAL_ADDRESS`), not Redis; if the Temporal server is
   unreachable, deploys, ticks and agent dispatch stall until it recovers.

**Rollback the control plane:**

If a recent code deploy broke the API, roll back to the previous
container image tag in the ECS task definition and force a new
deployment.

**Rollback:** the image rollback above is itself reversible: register the
newer task definition revision again and force a new deployment once the
fix is in.

---

## Cluster reports Degraded or Offline

The badge is derived from the keep-alive agent's heartbeat, not from a
control-plane poll. With the default 30 s interval: **Connected** within
1.5 intervals, **Degraded** after one missed interval, **Offline** after
three, **Never seen** when no heartbeat has ever landed (agent not
installed, or it never reached the control plane). Offline and Never
seen switch the Status, Health and Activity tabs to an offline empty
state.

**Signature:** `astroliftCluster(slug: "<slug>")` reports `heartbeatStatus`
`degraded` (last pulse older than 1.5 intervals), `offline` (older than 3
intervals) or `never_seen` (`lastHeartbeatAt` null); the console badge reads
**Degraded**, **Offline** or **Never seen**.

**Fix:**

1. Check the agent in the cluster:
   ```bash
   kubectl -n astrolift-system get deploy,pods
   ```
   An `ImagePullBackOff` after an `AGENT_IMAGE` change self-heals:
   `AgentReconcileTickWorkflow` re-applies the agent manifests every
   5 minutes.

2. Verify the cluster API endpoint is reachable from the Astrolift
   control plane:
   ```bash
   curl -k https://<cluster-api-endpoint>/healthz
   ```

3. Check whether the stored credentials have expired (token TTL,
   certificate expiry).

4. If the cluster is a cloud-managed cluster (EKS, GKE, AKS), check
   the cloud console for maintenance windows or control plane outages.

**Never seen on a freshly registered cluster:**

Install the agent, then read the server-side confirmation. The server
installs and verifies the agent before its staged credentials activate.

```bash
astro operator cluster install-agent --slug <slug> --request-file <path>
astro operator cluster agent-install-status --install-id <uuid>
```

**Degraded after credential rotation:**

There is no console path that replaces a cluster's stored credentials.
For a cloud cluster re-run the registration command from the backend
container to rewrite them from the cloud API, then **Refresh setup** on the
cluster header. See the cluster management runbook.

```bash
python manage.py register_tenant_cluster --slug <slug> --auto-discover-aws --aws-cluster-name <eks-name>
# or --auto-discover-azure / --auto-discover-gcp
```

**Rollback:** the agent install has nothing to undo; `register_tenant_cluster`
upserts by slug, so re-run it with the previous values to restore the prior
endpoint and auth shape.

---

## Webhook deliveries failing

Apps using push-to-deploy stop triggering deploys.

**Signature:** the delivery log on the source host, or
`/webhooks/[id]/deliveries/[deliveryId]`, shows the response Astrolift
returned: `401` with body `{"detail": "invalid signature"}` (signature
mismatch or missing) or `{"detail": "webhook secret not configured"}`;
`404` `{"detail": "app not found"}`; `403` `{"detail": "repository does not
match webhook target"}`; or no deliveries at all.

**Fix:**

**Check the webhook log:**

1. Go to **App detail → Settings → Webhooks**.
2. Look at recent deliveries. A delivery with a non-2xx response code
   means Astrolift received the event but returned an error; a 401 means
   the signing secret drifted.

**Webhook signing secret mismatch:**

The most common cause. When Astrolift re-registers the GitHub App or
the secret rotates:

1. Go to **Providers → Source** (`/providers#source`).
2. Rotate the signing secret on the affected connection
   (`rotateWebhookSecret(input: {connectionId})`) and set the revealed
   value on the source host's webhook.
3. Redeliver the failed delivery from the source host's webhook settings.

**Astrolift is not receiving webhooks:**

If recent deliveries show no entries at all, the webhook is not being
delivered to Astrolift. Verify:

- `APP_BASE_URL` in the backend environment matches the public
  hostname the source host is sending to.
- The host is reachable on the webhook path, which is mounted at the
  project root (no `/app/` prefix): `POST /api/webhooks/github/<app_guid>/`
  for app push-to-deploy; pipeline webhooks land on
  `POST /webhooks/pipelines/github/<org_slug>/` and
  `POST /webhooks/pipelines/gitlab/<org_slug>/`.

**Rollback:** a secret rotation is not reversible; if the new value was not
applied on the source host, rotate once more and set that value. `APP_BASE_URL`
changes are undone by restoring the previous value and redeploying the
webservice.

---

## A managed search service (OpenSearch) is unavailable

The control plane does not depend on OpenSearch: `FEATURE_OPENSEARCH` is
install-time inventory only, historical logs come from the cluster log
collector when one is installed, and activity feeds come from Postgres.
OpenSearch appears only as a provider-managed `search` / `vector_index`
resource attached to apps (the OpenSearch operator on `k8s_native`,
Amazon OpenSearch on AWS), so an outage affects those apps, not the
platform.

**Signature:** `_cluster/health` reports `status: red` (a primary shard is
unassigned) or the endpoint does not answer; the attached apps fail, the
control plane's `/health/` stays 200.

**Fix:**

**Check cluster health:**

```bash
curl http://<opensearch-host>:9200/_cluster/health | jq .status
```

Expected: `green` or `yellow`. `red` means at least one primary shard
is unassigned.

**Common causes:**

- Disk full: OpenSearch enters read-only mode when disk usage exceeds
  `cluster.routing.allocation.disk.watermark.high` (default 90%).
  Free disk space, then clear the read-only lock:
  ```bash
  curl -XPUT http://localhost:9200/_all/_settings \
    -H 'Content-Type: application/json' \
    -d '{"index.blocks.read_only_allow_delete": null}'
  ```
- JVM heap pressure: restart the OpenSearch service.
- Corrupted shard: run `_cat/shards?h=index,shard,state,docs,prirep`
  to identify the shard and follow the
  [OpenSearch shard recovery docs](https://opensearch.org/docs/latest/tuning-your-cluster/availability-and-recovery/).

**Rollback:** clearing the read-only block needs no rollback; a restart
needs none.

---

## Verify

```bash
# App: at least one ready pod, and the environment's running deployment is the expected one
astro app pods <app-slug> --env <env> --ready
astro ci status <deployment-id>
# Control plane: every checker reports working
curl -sf "https://<astrolift-host>/health/?format=json" | jq .
# Cluster: lifecycle managed, heartbeat connected
astro api graphql -q 'query { astroliftCluster(slug: "<slug>") { lifecycle heartbeatStatus lastHeartbeatAt } }'
# Deploys: nothing stuck
python manage.py temporal_ops list --status running
```

The app answers 200 at its URL, `/health/` is HTTP 200 with every checker
`working`, the cluster shows `managed` and `connected`, and `temporal_ops
list` shows no `DeployAppWorkflow` or `RollbackDeploymentWorkflow` older than
its activity bounds.

## Related

- [Deploy runbook](deploy.md)
- [Cluster management runbook](cluster-management.md)
- [Agent dispatch runbook](agent-dispatch.md)
