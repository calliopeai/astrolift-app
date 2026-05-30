import { ClipboardListIcon, PlayIcon, HistoryIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";

export const metadata = { title: "Tasks · Astrolift" };

/**
 * Tasks — one-off container task execution.
 *
 * Tasks are the third execution primitive after Jobs (scheduled/recurring)
 * and Deployments (long-running services). A Task is a single container run
 * with a defined command: database migrations, seed scripts, data exports,
 * manual interventions — anything that runs once and exits.
 *
 * Maps to Kubernetes `batch/v1 Job` with `completions=1`. Distinct from
 * CronJobs (recurring) and Deployments (persistent replicas).
 */
export default function TasksPage() {
  return (
    <PageShell
      title="Tasks"
      description="One-off container task execution — migrations, scripts, and manual interventions."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <PlayIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Run a task</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Execute any command inside a registered app's container image.
                Runs as a Kubernetes Job — isolated, audited, and observable
                from the same pod log stream as regular workloads.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <HistoryIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Task history</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Fleet-wide log of every task run: app, environment, command,
                exit code, duration, and triggering actor. Full stdout/stderr
                captured and linked from each row.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ClipboardListIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Task templates</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Save frequently-run commands as named templates. Operators can
                trigger a template from the UI without knowing the exact command —
                useful for migrations, cache flushes, and health checks.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
