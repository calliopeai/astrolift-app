"use client";

/**
 * Cluster > Activity: recent Temporal workflow runs targeting this
 * cluster + the lifecycle audit timeline pulled from MutationAuditLog.
 * "What's been happening here" rather than "what's its state right now".
 * Both grow, so each is a Feed in its panel (list rule 5): it scrolls in
 * its own frame and asks for older entries as the reader nears the end.
 */

import { CheckIcon, ClockIcon, GitBranchIcon, XIcon } from "lucide-react";

import { Feed } from "@/components/feed/Feed";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { useFormatters } from "@/lib/i18n/formatters";

import type { AuditRow, WorkflowRun } from "./types";
import type { useClusterLifecycleAudit } from "./use-cluster-lifecycle-audit";
import type { useRecentClusterWorkflows } from "./use-recent-cluster-workflows";

/** Load older on a field that takes a limit (useGrowingLimit); absent, the feed ends. */
type More = ReturnType<typeof useRecentClusterWorkflows>["more"];

export interface ClusterActivityBodyProps {
  workflows: Omit<ReturnType<typeof useRecentClusterWorkflows>, "more"> & { more?: More };
  lifecycle: Omit<ReturnType<typeof useClusterLifecycleAudit>, "more"> & { more?: More };
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
  more,
}: ClusterActivityBodyProps["workflows"]) {
  const fmt = useFormatters();

  return (
    <Panel
      span={6}
      icon={<GitBranchIcon className="size-4" />}
      title="Recent workflows"
      description="Temporal runs targeting this cluster — BringClusterIntoManagement, Refresh, Decommission, InstallClusterPrereqs, DriftDetection. Polls every 15s while a run is in flight."
      flush
    >
      <Feed<WorkflowRun>
        label="Recent workflows"
        items={runs}
        keyOf={(r) => r.workflowId + r.runId}
        loading={loading && runs.length === 0}
        error={error}
        onRetry={refetch}
        {...more}
        dense
        className="px-4"
        empty={{
          icon: <GitBranchIcon className="size-5" />,
          title: "No workflow runs recorded yet",
          description:
            "Operator actions like Bring into management, Refresh, or Install prereqs will appear here.",
        }}
        renderItem={(r) => {
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
            <div className="flex min-w-0 flex-wrap items-baseline gap-2 text-sm">
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
            </div>
          );
        }}
      />
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
  more,
}: ClusterActivityBodyProps["lifecycle"]) {
  const fmt = useFormatters();

  return (
    <Panel
      span={6}
      icon={<ClockIcon className="size-4" />}
      title="Lifecycle + activity"
      description="Every mutation that targeted this cluster — registered → managing → managed transitions, refreshes, prereq installs, decommission attempts. Sourced from the platform audit log."
      flush
    >
      <Feed<AuditRow>
        label="Lifecycle events"
        items={entries}
        keyOf={(e) => `${e.timestamp}-${e.operation}`}
        groupBy={{ day: (e) => e.timestamp }}
        loading={loading && entries.length === 0}
        error={error}
        onRetry={refetch}
        {...more}
        dense
        className="px-4"
        empty={{
          icon: <ClockIcon className="size-5" />,
          title: "No lifecycle events recorded yet",
          description: "Operator mutations against this cluster will show up here.",
        }}
        renderItem={(e) => (
          <div className="flex min-w-0 items-start gap-2">
            <span
              className={
                "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full " +
                (e.success ? "bg-success/15 text-success-fg" : "bg-destructive/15 text-destructive")
              }
            >
              {e.success ? <CheckIcon className="size-3" /> : <XIcon className="size-3" />}
            </span>
            <div className="min-w-0 flex-1">
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
            </div>
          </div>
        )}
      />
    </Panel>
  );
}
