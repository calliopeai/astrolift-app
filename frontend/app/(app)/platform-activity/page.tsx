import { ActivityIcon, RefreshCwIcon, TerminalIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata = { title: "Platform Activity · Astrolift" };

/**
 * Platform Activity — Temporal workflow engine observability.
 *
 * Surfaces the platform's internal execution engine: running Temporal
 * workflow instances, their state, duration, and any failures. This is
 * the SRE/admin surface for diagnosing stuck workflows (e.g. a
 * DeployAppWorkflow that hasn't progressed), terminating runaway
 * instances, and auditing automated platform operations.
 *
 * Distinct from user-defined Workflow Definitions (BUILD section) and
 * from application-level Jobs (RUN section).
 */
export default function PlatformActivityPage() {
  return (
    <PageShell
      title="Platform Activity"
      description="Live Temporal workflow engine — running platform operations, stuck instances, and execution history."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ActivityIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Running instances</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Live view of all Temporal workflow instances: DeployAppWorkflow,
                OnboardAppWorkflow, BringClusterIntoManagementWorkflow, and
                platform-level scheduled sweeps. Filter by type, status, or app.
              </p>
            </div>
            {process.env.NEXT_PUBLIC_TEMPORAL_UI_URL && (
              <Button variant="outline" size="sm" className="w-fit gap-1.5" asChild>
                <a
                  href={process.env.NEXT_PUBLIC_TEMPORAL_UI_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open Temporal UI
                </a>
              </Button>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <TerminalIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Terminate / cancel</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Cancel or terminate stuck workflow instances without leaving the
                control plane. Cancellation drains gracefully; termination is
                immediate. Both require admin permission.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <RefreshCwIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Execution history</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Searchable history of completed, failed, and timed-out platform
                operations. Linked to the audit log — every workflow run carries
                an actor and a reason.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
