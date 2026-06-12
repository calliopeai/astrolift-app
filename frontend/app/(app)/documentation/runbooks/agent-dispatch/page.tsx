import Link from "next/link";

import { Separator } from "@/components/ui/separator";
import { ArrowLeftIcon } from "lucide-react";

export const metadata = {
  title: "Agent dispatch · Runbooks · Documentation · Astrolift",
};

const dispatchCli = `astro agent dispatch <agent-slug> \\
  --input key=value \\
  --input key2=value2

# Wait for completion and stream the log:
astro agent dispatch <agent-slug> --input key=value --wait --tail`;

const dispatchApi = `mutation {
  dispatchAgent(agentSlug: "<slug>", input: {key: "value"}) {
    ok
    task {
      id
      status
    }
    errors { field messages }
  }
}`;

const monitorCli = `astro tasks list --agent <agent-slug> --limit 20
astro tasks logs <task-id>`;

const cancelCli = `astro tasks cancel <task-id>`;

const workerCheck = `astro workers status

# Flower dashboard (self-hosted):
# http://<astrolift-host>:5555`;

export default function AgentDispatchRunbookPage() {
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
        <h1 className="text-2xl font-semibold">Agent dispatch</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Dispatch, monitor, cancel, and recover stuck agent tasks.
        </p>
      </div>

      <div className="bg-muted/40 text-muted-foreground rounded-md border p-4 text-sm leading-relaxed">
        <strong className="text-foreground">What agents do.</strong>{" "}
        Agents are operator-defined units of autonomous work — database
        migrations, canary analysis, smoke tests, infrastructure provisioning,
        or any task that runs on a schedule or in response to a platform event.
      </div>

      <Separator />

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Dispatch an agent task</h2>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the UI</h3>
          <ol className="text-muted-foreground flex flex-col gap-1 text-sm">
            <li>1. Go to <strong>Agents</strong> in the left sidebar.</li>
            <li>2. Find the agent in the gallery (or search by name / skill).</li>
            <li>3. Click <strong>Dispatch</strong>.</li>
            <li>4. Fill in any input parameters the agent requires.</li>
            <li>5. Click <strong>Run</strong>. The task appears in <strong>Tasks</strong> immediately.</li>
          </ol>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">From the CLI or API</h3>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{dispatchCli}</code>
          </pre>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{dispatchApi}</code>
          </pre>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Monitor a running task</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The task detail page (reachable from <strong>Tasks → [id]</strong>)
          shows status, a real-time log stream, input parameters, and any
          structured output the agent emits on success. Status sequence:
          Queued → Running → Completed | Failed | Cancelled.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{monitorCli}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Cancel a running task</h2>
        <ol className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            1. Open the task detail page. Click <strong>Cancel task</strong>.
            Confirm.
          </li>
          <li>
            2. Astrolift sends a soft cancel signal to the worker. The worker
            has up to 30 seconds to clean up before a hard kill is sent.
          </li>
        </ol>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{cancelCli}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Stuck tasks</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          A task is stuck if it stays in <strong>Running</strong> status
          longer than its configured timeout (default: 1 hour). First check
          worker health:
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{workerCheck}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          If workers are healthy but the task has made no progress, cancel
          the task and investigate the agent code for an operation that
          blocks indefinitely (database query without timeout, external API
          call without deadline, resource lock). Fix and re-dispatch.
        </p>
        <p className="text-muted-foreground text-sm leading-relaxed">
          If the Celery worker holding the task lease crashed, the task
          stays in Running status until the visibility timeout expires
          (default: 1h) and then re-queues automatically if{" "}
          <code>max_retries &gt; 0</code>.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Common failure modes</h2>
        <div className="overflow-x-auto">
          <table className="text-muted-foreground w-full text-xs">
            <thead>
              <tr className="border-b text-left">
                <th className="text-foreground pb-2 pr-4 font-medium">Failure</th>
                <th className="text-foreground pb-2 pr-4 font-medium">Likely cause</th>
                <th className="text-foreground pb-2 font-medium">Resolution</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              <tr>
                <td className="py-2 pr-4 align-top"><code>ImportError</code> immediately</td>
                <td className="py-2 pr-4 align-top">Module not installed in worker</td>
                <td className="py-2 align-top">Add dependency, rebuild worker image, redeploy</td>
              </tr>
              <tr>
                <td className="py-2 pr-4 align-top">Permission denied</td>
                <td className="py-2 pr-4 align-top">Missing credential or API token</td>
                <td className="py-2 align-top">Add secret under <strong>Administration → Secrets</strong></td>
              </tr>
              <tr>
                <td className="py-2 pr-4 align-top">Task completes, wrong output</td>
                <td className="py-2 pr-4 align-top">Logic error in agent code</td>
                <td className="py-2 align-top">Review logs, fix code, redeploy</td>
              </tr>
              <tr>
                <td className="py-2 pr-4 align-top">Fails after N retries</td>
                <td className="py-2 pr-4 align-top">Transient external dependency unreachable</td>
                <td className="py-2 align-top">Restore dependency, dispatch manually</td>
              </tr>
              <tr>
                <td className="py-2 pr-4 align-top">Queued indefinitely</td>
                <td className="py-2 pr-4 align-top">No workers subscribed to queue</td>
                <td className="py-2 align-top">Confirm workers are running and subscribed to <code>default</code> queue</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/runbooks/incident-response"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Incident response
            </Link>{" "}
            — broader triage guide including worker outages.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/deploy"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Deploy workflow
            </Link>{" "}
            — agents can be triggered as deploy lifecycle hooks.
          </li>
        </ul>
      </section>
    </article>
  );
}
