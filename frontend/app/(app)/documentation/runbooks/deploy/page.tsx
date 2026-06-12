import Link from "next/link";

import { Separator } from "@/components/ui/separator";
import { ArrowLeftIcon } from "lucide-react";

export const metadata = {
  title: "Deploy workflow · Runbooks · Documentation · Astrolift",
};

const deployUiSteps = `# From the CLI:
astro deploy --app <slug> --cluster <cluster-slug>

# Wait for completion and tail the log:
astro deploy --app <slug> --cluster <cluster-slug> --wait --tail`;

const deployApi = `mutation {
  deployApp(appSlug: "<slug>", clusterSlug: "<cluster>") {
    ok
    deployment {
      id
      status
    }
    errors { field messages }
  }
}`;

const deployMonitor = `astro deployments list --app <slug> --limit 10
astro deployments logs <deployment-id>`;

const rollbackCli = `astro rollback --app <slug> --deployment <deployment-id>`;

const zeroDtToml = `# In astrolift.toml — define a readiness probe:
[health]
readiness_path    = "/health"
liveness_path     = "/health"
initial_delay     = 5
period            = 10
timeout           = 3
failure_threshold = 3`;

export default function DeployRunbookPage() {
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
        <h1 className="text-2xl font-semibold">Deploy workflow</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Standard deploy flow, monitoring, rollback, and zero-downtime
          considerations.
        </p>
      </div>

      <Separator />

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Deploy an app</h2>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the UI</h3>
          <ol className="text-muted-foreground flex flex-col gap-1 text-sm">
            <li>1. Open <strong>Apps</strong> and click the target app.</li>
            <li>2. Click <strong>Deploy</strong> (top-right).</li>
            <li>3. Select the target cluster. Confirm.</li>
            <li>4. Watch the deploy log stream on the deploy detail page.</li>
            <li>5. When status shows <strong>Running</strong>, verify via the <strong>Open</strong> link.</li>
          </ol>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From a git push (source-connected apps)</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Push to the branch configured as the deploy branch. Astrolift
            receives the webhook, creates a build job, and starts a deploy
            automatically. The deploy appears in the Deployments list within
            5–10 seconds of the push.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the CLI or API</h3>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{deployUiSteps}</code>
          </pre>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{deployApi}</code>
          </pre>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Monitor a deploy</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The deploy detail page (reachable from <strong>Deployments → [id]</strong>)
          shows status, a real-time log stream, and cluster events for the
          workload. The status sequence is: Queued → Building → Deploying →
          Running | Failed.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{deployMonitor}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Roll back to a previous deploy</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Rollback re-applies the manifest and image tag of an earlier
          successful deploy. It does not revert application data or
          environment variable changes made after that deploy.
        </p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the UI</h3>
          <ol className="text-muted-foreground flex flex-col gap-1 text-sm">
            <li>1. Open <strong>App detail → Deployments</strong>.</li>
            <li>2. Find the deploy you want to restore. Click the row.</li>
            <li>3. Click <strong>Roll back to this deploy</strong>. Confirm.</li>
          </ol>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Astrolift creates a new deploy pointing at the historical manifest
            snapshot and image digest. The rollback deploy appears in the list
            with a <strong>Rollback</strong> badge.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the CLI</h3>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{rollbackCli}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            A rollback that reuses cached image layers completes in under
            60 seconds. A cold cluster adds a pull duration.
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Failed deploy: immediate remediation</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">Build failure.</strong>{" "}
            The deploy log shows the Docker build output. Fix the Dockerfile,
            push a new commit, and a new deploy triggers automatically.
          </li>
          <li>
            <strong className="text-foreground">ImagePullBackOff.</strong>{" "}
            The cluster cannot pull the image. Verify the image tag exists in
            the registry and that Astrolift has the registry credentials
            configured under <strong>Settings → Registries</strong>.
          </li>
          <li>
            <strong className="text-foreground">CrashLoopBackOff.</strong>{" "}
            The container starts and crashes. Check{" "}
            <strong>App detail → Logs</strong> for the error before the crash.
            Common causes: missing required env var, database not reachable at
            startup, port mismatch.
          </li>
          <li>
            <strong className="text-foreground">OOMKilled.</strong>{" "}
            The container exceeded its memory limit. Raise the memory limit in{" "}
            <strong>App detail → Resources</strong> and redeploy.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Zero-downtime deploy</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift performs rolling updates by default. For zero-downtime,
          your app must define a readiness probe and handle{" "}
          <code>SIGTERM</code> gracefully within the 30-second
          termination window.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{zeroDtToml}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/runbooks/cluster-management"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster management
            </Link>{" "}
            — register, maintain, and decommission clusters.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/incident-response"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Incident response
            </Link>{" "}
            — triage guide for common failure modes.
          </li>
          <li>
            <Link
              href="/documentation/quickstart"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Quickstart
            </Link>{" "}
            — end-to-end guide for the first deploy.
          </li>
        </ul>
      </section>
    </article>
  );
}
