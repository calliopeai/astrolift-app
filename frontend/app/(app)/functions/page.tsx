import { BoltIcon, ExternalLinkIcon, ScaleIcon, ZapIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata = { title: "Functions · Astrolift" };

/**
 * Functions — event-driven, short-lived container invocations.
 *
 * The seventh Astrolift runtime primitive alongside Apps, Agents,
 * Workflows, Jobs, Tasks, and scheduled Deployments.
 *
 * Functions differ from Tasks in trigger model and scale behavior:
 * - Trigger: HTTP request, queue message, webhook, or event
 * - Duration: milliseconds to seconds (not minutes)
 * - Scale: horizontal to zero between invocations (Knative Serving)
 * - Billing model: per-invocation, not per-pod-hour
 *
 * Same image build pipeline, same cluster, same secrets model as every
 * other Astrolift workload. The runtime layer (Knative or equivalent)
 * handles cold starts, warm pools, and concurrency limits.
 *
 * Gateway page — Functions require Knative Serving on the tenant cluster.
 */
export default function FunctionsPage() {
  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <BoltIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Event-driven triggers</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Invoke functions via HTTP, SQS/SNS messages, webhooks, or
                platform events. Each invocation runs in an isolated container
                — same image, same secrets, full audit trail.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ScaleIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Scale to zero</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Functions scale down to zero replicas between invocations and
                spin up in milliseconds on demand. Powered by Knative Serving
                on the tenant cluster — no idle pod cost.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ZapIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Same deploy pipeline</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Functions are declared in <code>astrolift.toml</code> as
                <code> kind: function</code> workloads. Same image build,
                same secrets injection, same approval gates as every other
                Astrolift workload.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit gap-1.5" asChild>
              <a
                href="https://astrolift.ai/roadmap"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLinkIcon className="size-3.5" />
                View roadmap
              </a>
            </Button>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
