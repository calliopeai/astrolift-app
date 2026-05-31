import Link from "next/link";
import { BotIcon, ExternalLinkIcon, RadioIcon, ZapIcon, LayoutListIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata = { title: "Agents · Astrolift" };

/**
 * Agents — dispatch and schedule AI agents as container workloads.
 *
 * Astrolift is a container runtime management platform. Agents are
 * first-class runtime primitives alongside apps and workflows — they
 * run inside the same EKS clusters, share the same deployment pipeline,
 * and surface here as a dedicated fleet view: dispatched instances,
 * scheduling policies, and run history.
 */
export default function AgentsPage() {
  return (
    <PageShell
      title="Agents"
      description="Dispatch, schedule, and monitor AI agent workloads across the fleet."
      actions={
        <Button asChild size="sm">
          <Link href="/agents/gallery">
            <RadioIcon className="mr-1 h-4 w-4" /> Active gallery
          </Link>
        </Button>
      }
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ZapIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Agent dispatch</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Trigger agent runs on demand or on a schedule. Each dispatch
                creates an isolated container workload on the tenant cluster —
                same deploy pipeline, full observability stack.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <LayoutListIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Run history</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Fleet-wide view of agent runs: status, duration, input/output
                summaries, and links to pod logs. Filter by agent, status, or
                triggering user.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <BotIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Agent registry</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Register agent workloads from your app manifests. Agents follow
                the same image build and promotion path as other workloads —
                versioned, audited, and rollback-capable.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
