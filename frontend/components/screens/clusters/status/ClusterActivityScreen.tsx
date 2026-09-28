"use client";

/**
 * Cluster > Activity: recent Temporal workflow runs targeting this
 * cluster + the lifecycle audit timeline pulled from MutationAuditLog.
 * "What's been happening here" rather than "what's its state right now".
 */

import { CheckIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useClusterLifecycleAudit } from "./use-cluster-lifecycle-audit";
import type { useRecentClusterWorkflows } from "./use-recent-cluster-workflows";

export interface ClusterActivityBodyProps {
  workflows: ReturnType<typeof useRecentClusterWorkflows>;
  lifecycle: ReturnType<typeof useClusterLifecycleAudit>;
}

/** The Activity tab body: recent workflows over the lifecycle timeline. */
export function ClusterActivityBody({ workflows, lifecycle }: ClusterActivityBodyProps) {
  return (
    <>
      <ActivityWorkflowsCard {...workflows} />
      <ActivityLifecycleCard {...lifecycle} />
    </>
  );
}

// ─── Recent workflows card (#394) ────────────────────────────────────────
// Pulled live from Temporal's visibility API filtered by workflow ids
// that reference this cluster's guid. Empty when Temporal is disabled
// or the query fails — identical empty-state copy in both cases.

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  RUNNING: "secondary",
  COMPLETED: "default",
  FAILED: "destructive",
  CANCELED: "outline",
  TERMINATED: "destructive",
  TIMED_OUT: "destructive",
  CONTINUED_AS_NEW: "outline",
};

function ActivityWorkflowsCard({ runs, loading }: ReturnType<typeof useRecentClusterWorkflows>) {
  const fmt = useFormatters();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent workflows</CardTitle>
        <CardDescription>
          Temporal runs targeting this cluster — BringClusterInto- Management, Refresh,
          Decommission, InstallClusterPrereqs, DriftDetection. Polls every 15s while a run is in
          flight.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && runs.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : runs.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No workflow runs recorded yet. Operator actions like Bring into management, Refresh, or
            Install prereqs will appear here.
          </p>
        ) : (
          <ul className="space-y-2">
            {runs.map((r) => {
              const duration =
                r.closedAt && r.startedAt
                  ? Math.max(
                      0,
                      Math.round(
                        (new Date(r.closedAt).getTime() - new Date(r.startedAt).getTime()) / 1000
                      )
                    )
                  : null;
              return (
                <li
                  key={r.workflowId + r.runId}
                  className="border-border flex flex-wrap items-baseline gap-2 rounded-md border p-2 text-sm"
                >
                  <code className="font-mono text-xs">{r.workflowType}</code>
                  <Badge variant={STATUS_VARIANT[r.status] ?? "outline"} className="text-2xs">
                    {r.status}
                  </Badge>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {r.startedAt ? fmt.formatDateTime(r.startedAt) : "—"}
                    {duration !== null && <span className="opacity-60"> · {duration}s</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Lifecycle timeline card (#68 slice 2) ───────────────────────────────
// Backed by ``astroliftClusterLifecycleAudit`` — filters MutationAuditLog
// for cluster-targeted operations referencing this cluster's guid/slug
// and renders a vertical timeline (operation, outcome, actor, time).

function ActivityLifecycleCard({ entries, loading }: ReturnType<typeof useClusterLifecycleAudit>) {
  const fmt = useFormatters();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Lifecycle + activity</CardTitle>
        <CardDescription>
          Every mutation that targeted this cluster — registered → managing → managed transitions,
          refreshes, prereq installs, decommission attempts. Sourced from the platform audit log.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && entries.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No lifecycle events recorded yet. Operator mutations against this cluster will show up
            here.
          </p>
        ) : (
          <ol className="border-border relative space-y-3 border-l pl-6">
            {entries.map((e, i) => (
              <li key={`${e.timestamp}-${i}`} className="relative">
                <span
                  className={
                    "absolute -left-[27px] flex size-5 items-center justify-center rounded-full " +
                    (e.success
                      ? "bg-success/15 text-success-fg"
                      : "bg-destructive/15 text-destructive")
                  }
                >
                  {e.success ? <CheckIcon className="size-3" /> : <XIcon className="size-3" />}
                </span>
                <div className="flex flex-wrap items-baseline gap-2 text-sm">
                  <code className="font-mono text-xs">{e.operation}</code>
                  {e.actor && <span className="text-muted-foreground text-xs">by {e.actor}</span>}
                  <span className="text-muted-foreground ml-auto text-xs">
                    {fmt.formatDateTime(e.timestamp)}
                  </span>
                </div>
                {!e.success && e.errors.length > 0 && (
                  <p className="text-destructive mt-1 line-clamp-2 text-xs">{e.errors[0]}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
