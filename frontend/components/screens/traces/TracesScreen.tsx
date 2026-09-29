import { ActivityIcon, GitBranchIcon, ZoomInIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";

/** /traces: fleet-wide trace explorer gateway; per-app traces live in each app's Observability tab. */
export function TracesScreen() {
  return (
    <PageShell
      title="Traces"
      description="Distributed trace explorer across all registered apps. Understand latency, errors, and service dependencies."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ZoomInIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Fleet trace search</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Search traces by service, operation, status code, and duration across all apps.
                Click into any trace to see a waterfall of spans.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ActivityIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Per-app traces (available now)</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Trace data is already collected and visible per-app. Navigate to any app and open
                the <strong>Observability</strong> tab to explore recent traces and span waterfalls.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <GitBranchIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Service map</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Automatically derived service dependency graph from trace data. Surfaces error rates
                and p99 latency on every edge — identify bottlenecks without reading individual
                spans.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
