"use client";

import { useTranslations } from "next-intl";
import { useFormatters } from "@/lib/i18n/formatters";
import { localizedPipelineToken } from "./pipelines-list";
import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";

import { ActivityIcon, DownloadIcon, ScrollIcon, SettingsIcon, UsersIcon } from "lucide-react";
import type * as React from "react";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineDag } from "@/components/viz";

import {
  PIPELINE_DETAIL_TABS,
  type usePipelineDetail,
  type useRunGraph,
} from "./use-pipeline-detail";

export type PipelineDetailScreenProps = Omit<
  ReturnType<typeof usePipelineDetail>,
  "onCancelRun" | "cancellationDialog"
> & {
  onCancelRun?: (id: string) => Promise<void>;
  cancellationDialog?: React.ReactNode;
  pipelineId?: string;
  /** The latest run's graph (a RunGraphView behind its hook), keyed on the run id. */
  runGraph?: React.ReactNode;
  /** The Secrets tab (a PipelineSecretsView behind its hook). */
  secrets: React.ReactNode;
};

/** A pipeline's detail screen: tab bar, the Runs tab, placeholders, and Secrets. */
export function PipelineDetailScreen({
  pipeline,
  pipelineLoading,
  pipelineError,
  onRetryPipeline,
  tab,
  onTabChange,
  runs,
  runsLoading,
  runsError,
  onRetryRuns,
  runGraph,
  secrets,
  pipelineId,
  onCancelRun,
  cancellationDialog,
}: PipelineDetailScreenProps) {
  const fmt = useFormatters();
  const t = useTranslations("ReviewedPipelineStart");
  const copy = useTranslations("PipelineUI");
  return (
    <PageShell title={pipeline?.name ?? copy("pipeline")} description={copy("detail.description")}>
      <div className="space-y-4">
        {cancellationDialog}
        {pipelineLoading && (
          <Skeleton className="h-5 w-48" aria-label={copy("detail.loadingName")} />
        )}
        <QueryError
          title={copy("detail.loadFailed")}
          retryLabel={copy("retry")}
          error={pipelineError}
          onRetry={onRetryPipeline}
        />
        {!pipelineLoading && !pipelineError && !pipeline && (
          <p className="text-muted-foreground text-sm">{copy("detail.unavailable")}</p>
        )}
        {/* Tab bar */}
        <div className="flex gap-1 border-b pb-0">
          {PIPELINE_DETAIL_TABS.map((t) =>
            t === "secrets" && pipelineId ? (
              <Link
                key={t}
                href={`/pipelines/${pipelineId}/secrets`}
                aria-current={tab === t ? "page" : undefined}
                className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${t === tab ? "border-primary text-foreground" : "text-muted-foreground hover:text-foreground border-transparent"}`}
              >
                {copy(`tabs.${t}`)}
              </Link>
            ) : (
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
                {copy(`tabs.${t}`)}
              </button>
            )
          )}
        </div>

        {/* Runs tab — latest-run DAG + run list (#107) */}
        {tab === "runs" && (
          <div className="space-y-4">
            {runsLoading && runs.length === 0 && <Skeleton className="h-40 w-full" />}
            <QueryError
              title={copy("detail.runsFailed")}
              retryLabel={copy("retry")}
              error={runsError}
              onRetry={onRetryRuns}
            />
            {!runsLoading && !runsError && runs.length === 0 && (
              <EmptyState
                icon={<ActivityIcon className="size-5" />}
                title={copy("detail.noRuns")}
                description={copy("detail.noRunsDescription")}
              />
            )}
            {runs.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <ActivityIcon className="size-4" />
                  {copy("detail.latestRun", { number: runs[0].runNumber })}
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
                    {localizedPipelineToken(run.status, "status", copy)}
                  </Badge>
                  <span className="font-mono text-xs font-medium">#{run.runNumber}</span>
                  <span className="text-muted-foreground text-xs capitalize">
                    {localizedPipelineToken(run.triggerKind, "trigger", copy)}
                  </span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {run.triggerRef?.replace(/^refs\/heads\//, "")}
                  </span>
                  {onCancelRun && ["pending", "running"].includes(run.status) && (
                    <Button size="sm" variant="outline" onClick={() => void onCancelRun(run.id)}>
                      {t("cancelRequest")}
                    </Button>
                  )}
                  <span className="text-muted-foreground ml-auto text-xs">
                    {run.startedAt ? fmt.formatDateTime(run.startedAt) : "—"}
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
            title={copy("detail.logsTitle")}
            description={copy("detail.logsDescription")}
          />
        )}

        {/* Artifacts tab (#109) — inert placeholder (#912). */}
        {tab === "artifacts" && (
          <EmptyState
            icon={<DownloadIcon className="size-5" />}
            title={copy("detail.artifactsTitle")}
            description={copy("detail.artifactsDescription")}
          />
        )}

        {/* Triggers tab (#110) — inert placeholder (#912). */}
        {tab === "triggers" && (
          <EmptyState
            icon={<SettingsIcon className="size-5" />}
            title={copy("detail.triggersTitle")}
            description={copy("detail.triggersDescription")}
          />
        )}

        {/* Runners tab (#111) — inert placeholder (#912). */}
        {tab === "runners" && (
          <EmptyState
            icon={<UsersIcon className="size-5" />}
            title={copy("detail.runnersTitle")}
            description={copy("detail.runnersDescription")}
          />
        )}

        {/* Secrets tab (#100) */}
        {tab === "secrets" && secrets}
      </div>
    </PageShell>
  );
}

export type RunGraphViewProps = Pick<ReturnType<typeof useRunGraph>, "loading" | "stages"> &
  Partial<Omit<ReturnType<typeof useRunGraph>, "loading" | "stages">>;

/** A single run's jobs as a status-coloured DAG. */
export function RunGraphView({
  loading,
  stages,
  truncated,
  onLoadMore,
  loadingMore,
  pageError,
}: RunGraphViewProps) {
  const t = useTranslations("ReviewedPipelineStart");
  const copy = useTranslations("PipelineUI");
  if (loading) return <Skeleton className="h-64 w-full" aria-label={copy("detail.graphLoading")} />;
  if (stages.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed px-4 py-6 text-center text-sm">
        {copy("detail.graphEmpty")}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <PipelineDag stages={stages} height={320} ariaLabel={copy("detail.graphLabel")} />
      {truncated && (
        <>
          <p className="text-muted-foreground text-xs">{t("graphPartial")}</p>
          <Button variant="outline" size="sm" disabled={loadingMore} onClick={onLoadMore}>
            {t("loadMore")}
          </Button>
        </>
      )}
      {pageError && <p role="alert">{t("unavailable")}</p>}
    </div>
  );
}
