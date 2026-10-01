"use client";

/**
 * Cluster > Activity: recent Temporal workflow runs targeting this
 * cluster + the lifecycle audit timeline pulled from MutationAuditLog.
 * "What's been happening here" rather than "what's its state right now".
 * Both grow, so each is a Feed in its panel (list rule 5): it scrolls in
 * its own frame and asks for older entries as the reader nears the end.
 */

import { CheckIcon, ClockIcon, GitBranchIcon, XIcon } from "lucide-react";
import { useTranslations } from "next-intl";

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
// Empty results also cover disabled Temporal; they do not prove a complete census.

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  RUNNING: "secondary",
  COMPLETED: "default",
  FAILED: "destructive",
  CANCELED: "outline",
  TERMINATED: "destructive",
  TIMED_OUT: "destructive",
  CONTINUED_AS_NEW: "outline",
};

const STATUS_LABELS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELED: "canceled",
  TERMINATED: "terminated",
  TIMED_OUT: "timedOut",
  CONTINUED_AS_NEW: "continuedAsNew",
} as const;

function observedDate(value: string): Date | null {
  if (typeof value !== "string" || !value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

function observedDuration(startedAt: string, closedAt: string): number | null {
  const started = observedDate(startedAt);
  const closed = observedDate(closedAt);
  if (!started || !closed || closed.getTime() < started.getTime()) return null;
  return Math.round((closed.getTime() - started.getTime()) / 1000);
}

function ActivityWorkflowsCard({
  runs,
  loading,
  error,
  refetch,
  more,
}: ClusterActivityBodyProps["workflows"]) {
  const fmt = useFormatters();
  const t = useTranslations("clusterActivity");

  return (
    <Panel
      span={6}
      icon={<GitBranchIcon className="size-4" />}
      title={t("workflowTitle")}
      description={t("workflowDescription")}
      flush
    >
      <Feed<WorkflowRun>
        label={t("workflowTitle")}
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
          title: t("noRuns"),
          description: t("noRunsHelp"),
        }}
        renderItem={(r) => {
          const duration = observedDuration(r.startedAt, r.closedAt);
          const started = observedDate(r.startedAt);
          const known = typeof r.status === "string" && Object.hasOwn(STATUS_LABELS, r.status);
          const label = known
            ? t(STATUS_LABELS[r.status as keyof typeof STATUS_LABELS])
            : (typeof r.status === "string" && r.status) || t("unknown");
          return (
            <div className="flex min-w-0 flex-wrap items-baseline gap-2 text-sm">
              <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                {r.workflowType}
              </code>
              <Badge
                variant={known ? STATUS_VARIANT[r.status] : "outline"}
                className="text-2xs max-w-full min-w-0 font-mono"
                title={typeof r.status === "string" ? r.status : undefined}
              >
                <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
              </Badge>
              <span className="text-muted-foreground ml-auto font-mono text-xs" title={r.startedAt}>
                {started ? fmt.formatDateTime(started) : r.startedAt ? t("unknown") : "—"}
                {r.closedAt && (
                  <span className="opacity-60">
                    {" "}
                    · {duration !== null ? t("seconds", { count: duration }) : t("unknown")}
                  </span>
                )}
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
  const t = useTranslations("clusterActivity");

  return (
    <Panel
      span={6}
      icon={<ClockIcon className="size-4" />}
      title={t("lifecycleTitle")}
      description={t("lifecycleDescription")}
      flush
    >
      <Feed<AuditRow>
        label={t("lifecycleLabel")}
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
          title: t("noEvents"),
          description: t("noEventsHelp"),
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
                    {t.rich("actor", {
                      name: e.actor,
                      actor: (chunks) => <span className="font-mono">{chunks}</span>,
                    })}
                  </span>
                )}
                <span className="text-muted-foreground ml-auto font-mono text-xs">
                  {observedDate(e.timestamp) ? fmt.formatDateTime(e.timestamp) : t("unknown")}
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
