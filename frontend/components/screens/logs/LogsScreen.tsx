import { FilterIcon, SearchIcon, TerminalIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";

/** /logs: fleet-wide log search gateway; per-app logs live in each app's Console tab. */
export function LogsScreen() {
  return (
    <PageShell
      title="Logs"
      description="Fleet-wide log search and aggregation across all registered apps and workloads."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <SearchIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Log search</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Full-text search across all app logs with filters for severity, time range, app,
                workload, and pod. Results link back to the live pod stream.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <TerminalIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Per-app log streaming (available now)</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Live log tailing and in-browser shell access are already available from each
                app&apos;s <strong>Console</strong> tab. Navigate to any app and open the Console
                tab to start streaming.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <FilterIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Log export</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Export logs to your preferred sink — object storage, SIEM, or a third-party log
                aggregator. Configured per-org from the platform observability settings.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
