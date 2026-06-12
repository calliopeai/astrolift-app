import Link from "next/link";

import { Separator } from "@/components/ui/separator";
import { ArrowLeftIcon } from "lucide-react";

export const metadata = {
  title: "Incident response · Runbooks · Documentation · Astrolift",
};

const podCheck = `kubectl get pods -n <app-namespace>
# Expected: at least one pod Running and 1/1 ready

# If crashing — check logs from the previous container:
kubectl logs -n <app-namespace> <pod-name> --previous

# Pending pod — check events:
kubectl describe pod <pod-name> -n <app-namespace>

# Service has no endpoints (ingress 503):
kubectl get endpoints <app-slug> -n <app-namespace>`;

const ecsCheck = `aws ecs describe-services \\
  --cluster <cluster-name> \\
  --services astrolift-api \\
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'

# Tail API logs:
aws logs tail /ecs/astrolift-api --follow`;

const clusterReachability = `# From the control plane or a workstation:
curl -k https://<cluster-api-endpoint>/healthz

# Verify kubeconfig credentials:
kubectl --kubeconfig <path> get nodes`;

const opensearchCheck = `curl http://<opensearch-host>:9200/_cluster/health | jq .status
# green or yellow = healthy; red = primary shard unassigned

# Clear read-only lock after freeing disk:
curl -XPUT http://localhost:9200/_all/_settings \\
  -H 'Content-Type: application/json' \\
  -d '{"index.blocks.read_only_allow_delete": null}'`;

const webhookCheck = `# Verify APP_BASE_URL resolves to the public hostname:
curl https://<astrolift-host>/app/webhooks/scm/<provider>/
# Expected: 405 Method Not Allowed (GET not supported) — means the path exists`;

export default function IncidentResponseRunbookPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <Link
          href="/documentation/runbooks"
          className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-1.5 text-sm transition-colors"
        >
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Back to runbooks
        </Link>
        <h1 className="text-2xl font-semibold">Incident response</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Triage guide for the most common failure modes. Each section covers
          what you observe, how to confirm, and how to resolve.
        </p>
      </div>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">App is not reachable (502 / 503 / connection refused)</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The app URL returns an error but the Astrolift UI shows status
          Running (deploy succeeded).
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{podCheck}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2.5 text-sm">
          <li>
            <strong className="text-foreground">Pod Running but returning errors.</strong>{" "}
            The application crashes after startup. Check logs from the previous
            container. Common causes: missing required env var, database not
            reachable, port mismatch.
          </li>
          <li>
            <strong className="text-foreground">Pod in CrashLoopBackOff.</strong>{" "}
            Container starts and crashes repeatedly. The log lines just before
            the crash are the diagnostic. Fix the root cause and redeploy.
          </li>
          <li>
            <strong className="text-foreground">Pod in Pending.</strong>{" "}
            Cannot be scheduled. Common causes: insufficient CPU/memory on any
            node, node selector mismatch, PVC pending (no default storage class
            or quota exceeded).
          </li>
          <li>
            <strong className="text-foreground">Ingress returns 503 with no backend.</strong>{" "}
            Service has zero ready endpoints. Check that the readiness probe is
            passing and the Service selector matches the pod labels.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Deploy is stuck (Deploying for more than 10 minutes)</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Check the deploy log in the UI for the last logged line. If output
          has been silent for more than 5 minutes, the deploy worker may have
          crashed. Check Celery worker health from the Flower dashboard or CLI.
        </p>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Large images can take several minutes to pull on a cold cluster.
          Watch the pod events for{" "}
          <code>Pulling image &quot;...&quot;</code> messages. If the pull stalls
          for more than 10 minutes, verify registry connectivity from a cluster
          node.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Control plane is unreachable</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The Astrolift frontend is down or API requests time out.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{ecsCheck}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Check{" "}
            <code>https://{"<astrolift-host>"}/app/health/</code> — expected
            response: <code>{"{"}"status": "ok"{"}"}</code>.
          </li>
          <li>
            Check the database connection. If the API container cannot reach
            Postgres, it surfaces in health check or startup logs.
          </li>
          <li>
            Check Redis. Celery workers cannot connect to the broker if Redis
            is down. The API degrades but does not crash.
          </li>
          <li>
            If a recent code deploy broke the API, roll back to the previous
            container image tag in the ECS task definition.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Cluster reports Degraded or Unreachable</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          <strong>Degraded</strong> — API server unreachable for under 10
          minutes. <strong>Unreachable</strong> — no response for 10+ minutes.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{clusterReachability}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Verify the cluster API endpoint is reachable from the Astrolift
            control plane.
          </li>
          <li>
            Check whether kubeconfig credentials have expired (token TTL or
            certificate expiry).
          </li>
          <li>
            For cloud-managed clusters, check the cloud console for maintenance
            windows or control plane outages.
          </li>
          <li>
            After a credential rotation not yet updated in Astrolift, the cluster
            shows Degraded immediately — rotate credentials via{" "}
            <strong>Cluster detail → Settings → Credentials → Rotate credentials</strong>.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Webhook deliveries failing</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Push-to-deploy stops triggering deploys. Check the webhook log at{" "}
          <strong>App detail → Webhooks</strong>. A non-2xx response on recent
          deliveries means Astrolift received the event but returned an error.
        </p>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The most common cause is a signing secret mismatch. Go to{" "}
          <Link
            href="/settings/source-providers"
            className="text-foreground underline-offset-2 hover:underline"
          >
            Settings · Source providers
          </Link>{" "}
          and click <strong>Rotate signing secret</strong> on the affected host.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{webhookCheck}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">OpenSearch is unavailable</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Log search and cluster activity feeds stop working. Astrolift
          continues to buffer new log entries and indexes them once OpenSearch
          recovers.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{opensearchCheck}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Disk full.</strong>{" "}
            OpenSearch enters read-only mode when disk usage exceeds 90%.
            Free disk space, then clear the read-only lock (command above).
          </li>
          <li>
            <strong className="text-foreground">JVM heap pressure.</strong>{" "}
            Restart the OpenSearch service.
          </li>
          <li>
            <strong className="text-foreground">Red cluster health.</strong>{" "}
            At least one primary shard is unassigned. Run{" "}
            <code>GET /_cat/shards?h=index,shard,state,docs,prirep</code> to
            identify the shard and follow OpenSearch shard recovery procedures.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/runbooks/deploy"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Deploy workflow
            </Link>{" "}
            — rollback procedures for failed deploys.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/cluster-management"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster management
            </Link>{" "}
            — credential rotation and cluster health.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/agent-dispatch"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Agent dispatch
            </Link>{" "}
            — recovering stuck tasks and worker outages.
          </li>
        </ul>
      </section>
    </article>
  );
}
