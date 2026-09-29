"use client";

import { ActivityIcon, DownloadIcon, ScrollIcon, SettingsIcon, UsersIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineDag } from "@/components/viz";

import {
  PIPELINE_DETAIL_TABS,
  type PipelineDetailTab,
  type usePipelineDetail,
  type useRunGraph,
} from "./use-pipeline-detail";

const TAB_LABELS: Record<PipelineDetailTab, string> = {
  runs: "Runs",
  logs: "Logs",
  artifacts: "Artifacts",
  triggers: "Triggers",
  runners: "Runners",
  secrets: "Secrets",
};

export type PipelineDetailScreenProps = ReturnType<typeof usePipelineDetail> & {
  /** The latest run's graph (a RunGraphView behind its hook), keyed on the run id. */
  runGraph?: React.ReactNode;
  /** The Secrets tab (a PipelineSecretsView behind its hook). */
  secrets: React.ReactNode;
};

/** A pipeline's detail screen: tab bar, the Runs tab, placeholders, and Secrets. */
export function PipelineDetailScreen({
  tab,
  onTabChange,
  runs,
  runsLoading,
  runGraph,
  secrets,
}: PipelineDetailScreenProps) {
  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 border-b pb-0">
        {PIPELINE_DETAIL_TABS.map((t) => (
          <button
            key={t}
            onClick={() => onTabChange(t)}
            className={[
              "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
              t === tab
                ? "border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground border-transparent",
            ].join(" ")}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Runs tab — latest-run DAG + run list (#107) */}
      {tab === "runs" && (
        <div className="space-y-4">
          {runsLoading && runs.length === 0 && <Skeleton className="h-40 w-full" />}
          {!runsLoading && runs.length === 0 && (
            <EmptyState
              icon={<ActivityIcon className="size-5" />}
              title="No runs yet"
              description="Trigger a run via webhook or manual dispatch."
            />
          )}
          {runs.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm font-medium">
                <ActivityIcon className="size-4" />
                Latest run · #{runs[0].runNumber}
              </div>
              {runGraph}
            </div>
          )}
          <div className="space-y-2">
            {runs.map((run) => (
              <div
                key={run.id}
                className="flex items-center gap-3 rounded-md border px-4 py-3 text-sm"
              >
                <Badge
                  variant={
                    run.status === "success"
                      ? "default"
                      : run.status === "failure"
                        ? "destructive"
                        : "secondary"
                  }
                  className="shrink-0"
                >
                  {run.status}
                </Badge>
                <span className="font-mono text-xs font-medium">#{run.runNumber}</span>
                <span className="text-muted-foreground text-xs capitalize">{run.triggerKind}</span>
                <span className="text-muted-foreground font-mono text-xs">
                  {run.triggerRef?.replace(/^refs\/heads\//, "")}
                </span>
                <span className="text-muted-foreground ml-auto text-xs">
                  {run.startedAt ? new Date(run.startedAt).toLocaleString() : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Logs tab (#108) — placeholder; per-run log streaming isn't built yet.
          Copy must not instruct the impossible "select a run" action (#910). */}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Log streaming — coming soon"
          description="Step-by-step log streaming for pipeline runs isn't available yet. The latest run's stage graph is on the Runs tab."
        />
      )}

      {/* Artifacts tab (#109) — inert placeholder (#912). */}
      {tab === "artifacts" && (
        <EmptyState
          icon={<DownloadIcon className="size-5" />}
          title="Artifact browser — coming soon"
          description="Browsing run artifacts (build outputs, test reports, deployment packages) isn't available yet."
        />
      )}

      {/* Triggers tab (#110) — inert placeholder (#912). */}
      {tab === "triggers" && (
        <EmptyState
          icon={<SettingsIcon className="size-5" />}
          title="Trigger configuration — coming soon"
          description="Configuring webhook, scheduled, and manual-dispatch triggers from here isn't available yet."
        />
      )}

      {/* Runners tab (#111) — inert placeholder (#912). */}
      {tab === "runners" && (
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title="Runner management — coming soon"
          description="Registering and managing self-hosted runners for this pipeline isn't available yet."
        />
      )}

      {/* Secrets tab (#100) */}
      {tab === "secrets" && secrets}
    </div>
  );
}

export type RunGraphViewProps = ReturnType<typeof useRunGraph>;

/** A single run's jobs as a status-coloured DAG. */
export function RunGraphView({ loading, stages }: RunGraphViewProps) {
  if (loading) return <Skeleton className="h-64 w-full" />;
  if (stages.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed px-4 py-6 text-center text-sm">
        This run has no jobs to graph.
      </div>
    );
  }

  return <PipelineDag stages={stages} height={320} />;
}
