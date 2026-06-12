# Runbook: Incident response

Triage guide for the most common failure modes in Astrolift-managed
environments. Each section covers: what you observe, how to confirm,
and how to resolve.

---

## App is not reachable (502 / 503 / connection refused)

**What you observe:** The app URL returns an error. The Astrolift UI
shows the app status as **Running** (deploy succeeded) but the
endpoint is not working.

**Confirm the pod is actually running:**

```bash
kubectl get pods -n <app-namespace>
```

Expected: at least one pod in `Running` state and `1/1` ready.

**Pod is Running but returning errors:**

The application itself is crashing after startup. Check the
application logs:

```bash
kubectl logs -n <app-namespace> <pod-name> --previous
```

Or from the Astrolift UI: **App detail → Logs → [container]**.

Common causes: missing required env var, database connection refused
at startup, port mismatch.

**Pod is in CrashLoopBackOff:**

The container starts and crashes repeatedly. Check the log (above).
The most recent logs before the crash are usually the first place to
look. Once the root cause is fixed (env var, dependency, code),
redeploy.

**Pod is in Pending:**

The pod cannot be scheduled. Check cluster events:

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

**Ingress is not routing to the pod:**

The pod is Running but the ingress returns 503. Check that the
Kubernetes Service has endpoints:

```bash
kubectl get endpoints <app-slug> -n <app-namespace>
```

If the list is empty, the Service selector does not match the pod
labels, or the pod is not ready. Check readiness probe logs.

---

## Deploy is stuck (Deploying for more than 10 minutes)

**Check the deploy log in the UI** for the last logged line. If the
last line is more than 5 minutes old and the deploy is still active,
the deploy worker may have crashed.

**Check Celery worker health:**

From the platform admin or Flower dashboard, verify at least one
worker is active in the `default` queue.

**Force re-queue a stuck deploy (Django admin):**

1. Navigate to **Admin → Deployments → [deployment id]**.
2. If status is `deploying` but no task ID is set, set status to
   `pending` and save. The deploy re-queues on the next beat tick.

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

**Immediate checks (infrastructure team):**

1. Verify the Django API service is healthy:
   ```bash
   curl -sf https://<astrolift-host>/app/health/ | jq .
   ```
   Expected: `{"status": "ok"}`. If this fails, the backend is down.

2. Check ECS service status (if running on ECS Fargate):
   ```bash
   aws ecs describe-services \
     --cluster <cluster-name> \
     --services astrolift-api \
     --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'
   ```

3. Tail ECS logs:
   ```bash
   aws logs tail /ecs/astrolift-api --follow
   ```

4. Check the database connection. If the API container cannot reach
   Postgres, it surfaces in the health check response or in the logs
   immediately on startup.

5. Check Redis. Celery workers cannot connect to the broker if Redis
   is down. The API itself degrades but does not crash; background
   tasks queue until Redis recovers.

**Rollback the control plane:**

If a recent code deploy broke the API, roll back to the previous
container image tag in the ECS task definition and force a new
deployment.

---

## Cluster reports Degraded or Unreachable

**Degraded** means the Astrolift agent cannot reach the cluster's
Kubernetes API server but the failure is recent (within the last 10
minutes).

**Unreachable** means the API server has not responded for 10+ minutes.

**Immediate checks:**

1. Verify the cluster API endpoint is reachable from the Astrolift
   control plane:
   ```bash
   curl -k https://<cluster-api-endpoint>/healthz
   ```

2. Check whether the kubeconfig credentials have expired (token TTL,
   certificate expiry).

3. If the cluster is a cloud-managed cluster (EKS, GKE, AKE), check
   the cloud console for maintenance windows or control plane outages.

**Degraded after credential rotation:**

If credentials were rotated but not updated in Astrolift, the cluster
shows Degraded immediately. Rotate credentials via:

**Cluster detail → Settings → Credentials → Rotate credentials**

---

## Webhook deliveries failing

Apps using push-to-deploy stop triggering deploys.

**Check the webhook log:**

1. Go to **App detail → Webhooks**.
2. Look at recent deliveries. A delivery with a non-2xx response code
   means Astrolift received the event but returned an error.

**Webhook signing secret mismatch:**

The most common cause. When Astrolift re-registers the GitHub App or
the secret rotates:

1. Go to **Settings → Source providers**.
2. Click **Rotate signing secret** on the affected host.
3. GitHub/GitLab retries the last failed delivery automatically within
   a few minutes; or click **Redeliver** on the failed delivery row.

**Astrolift is not receiving webhooks:**

If recent deliveries show no entries at all, the webhook is not being
delivered to Astrolift. Verify:

- `APP_BASE_URL` in the backend environment matches the public
  hostname GitHub/GitLab is sending to.
- The host is reachable on the webhook path:
  `POST /app/webhooks/scm/<provider>/`

---

## OpenSearch is unavailable

Log search and cluster activity feeds stop working. Astrolift degrades
gracefully — it continues to write new log entries to the database
buffer and indexes them once OpenSearch recovers.

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

---

## Related

- [Deploy runbook](deploy.md)
- [Cluster management runbook](cluster-management.md)
- [Agent dispatch runbook](agent-dispatch.md)
