"use client";

/**
 * Cluster > Activity: recent Temporal workflow runs targeting this
 * cluster + the lifecycle audit timeline pulled from MutationAuditLog.
 * "What's been happening here" rather than "what's its state right now".
 */

import { CheckIcon, ClockIcon, GitBranchIcon, XIcon } from "lucide-react";

import { Panel, PanelGrid, SkeletonRows } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useClusterLifecycleAudit } from "./use-cluster-lifecycle-audit";
import type { useRecentClusterWorkflows } from "./use-recent-cluster-workflows";

export interface ClusterActivityBodyProps {
  workflows: ReturnType<typeof useRecentClusterWorkflows>;
  lifecycle: ReturnType<typeof useClusterLifecycleAudit>;
}

/** The Activity tab body: recent workflows beside the lifecycle timeline. */
export function ClusterActivityBody({ workflows, lifecycle }: ClusterActivityBodyProps) {
  return (
    <PanelGrid>
      <ActivityWorkflowsCard {...workflows} />
      <ActivityLifecycleCard {...lifecycle} />
    </PanelGrid>
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

function ActivityWorkflowsCard({
  runs,
  loading,
  error,
  refetch,
}: ReturnType<typeof useRecentClusterWorkflows>) {
  const fmt = useFormatters();

  return (
    <Panel
      span={6}
      icon={<GitBranchIcon className="size-4" />}
      title="Recent workflows"
      description="Temporal runs targeting this cluster — BringClusterInto- Management, Refresh, Decommission, InstallClusterPrereqs, DriftDetection. Polls every 15s while a run is in flight."
      loading={loading && runs.length === 0}
      skeleton={<Skeleton className="h-24 w-full" />}
      error={runs.length === 0 ? error : null}
      onRetry={refetch}
      empty={
        runs.length === 0
          ? {
              icon: <GitBranchIcon className="size-5" />,
              title: "No workflow runs recorded yet",
              description:
                "Operator actions like Bring into management, Refresh, or Install prereqs will appear here.",
            }
          : null
      }
    >
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
              className="border-border flex min-w-0 flex-wrap items-baseline gap-2 rounded-md border p-2 text-sm"
            >
              <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                {r.workflowType}
              </code>
              <Badge variant={STATUS_VARIANT[r.status] ?? "outline"} className="text-2xs font-mono">
                {r.status}
              </Badge>
              <span className="text-muted-foreground ml-auto font-mono text-xs">
                {r.startedAt ? fmt.formatDateTime(r.startedAt) : "—"}
                {duration !== null && <span className="opacity-60"> · {duration}s</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

// ─── Lifecycle timeline card (#68 slice 2) ───────────────────────────────
// Backed by ``astroliftClusterLifecycleAudit`` — filters MutationAuditLog
// for cluster-targeted operations referencing this cluster's guid/slug
// and renders a vertical timeline (operation, outcome, actor, time).

function ActivityLifecycleCard({
  entries,
  loading,
  error,
  refetch,
}: ReturnType<typeof useClusterLifecycleAudit>) {
  const fmt = useFormatters();

  return (
    <Panel
      span={6}
      icon={<ClockIcon className="size-4" />}
      title="Lifecycle + activity"
      description="Every mutation that targeted this cluster — registered → managing → managed transitions, refreshes, prereq installs, decommission attempts. Sourced from the platform audit log."
      loading={loading && entries.length === 0}
      skeleton={<SkeletonRows />}
      error={entries.length === 0 ? error : null}
      onRetry={refetch}
      empty={
        entries.length === 0
          ? {
              icon: <ClockIcon className="size-5" />,
              title: "No lifecycle events recorded yet",
              description: "Operator mutations against this cluster will show up here.",
            }
          : null
      }
    >
      <ol className="border-border relative space-y-3 border-l pl-6">
        {entries.map((e, i) => (
          <li key={`${e.timestamp}-${i}`} className="relative min-w-0">
            <span
              className={
                "absolute -left-[27px] flex size-5 items-center justify-center rounded-full " +
                (e.success ? "bg-success/15 text-success-fg" : "bg-destructive/15 text-destructive")
              }
            >
              {e.success ? <CheckIcon className="size-3" /> : <XIcon className="size-3" />}
            </span>
            <div className="flex min-w-0 flex-wrap items-baseline gap-2 text-sm">
              <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                {e.operation}
              </code>
              {e.actor && (
                <span className="text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]">
                  by <span className="font-mono">{e.actor}</span>
                </span>
              )}
              <span className="text-muted-foreground ml-auto font-mono text-xs">
                {fmt.formatDateTime(e.timestamp)}
              </span>
            </div>
            {!e.success && e.errors.length > 0 && (
              <p className="text-destructive mt-1 line-clamp-2 text-xs [overflow-wrap:anywhere]">
                {e.errors[0]}
              </p>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}
