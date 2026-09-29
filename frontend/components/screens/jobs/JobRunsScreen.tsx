"use client";

import { HistoryIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { appsListCrumbs } from "@/components/screens/deployments/apps-area";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatDuration } from "./jobs-format";
import { jobRunStatus } from "./jobs-list";
import type { useJobRuns } from "./use-job-runs";

export type JobRunsScreenProps = ReturnType<typeof useJobRuns>;

/**
 * Apps › Scheduled jobs › Runs (spec 44 §4.4, §5.1): every job run, or one
 * job's when the app and job chips are set (a job row opens it that way).
 * Views All · Mine · Failed, cursor paged, live. Pure view; the data half is
 * useJobRuns.
 */
export function JobRunsScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
}: JobRunsScreenProps) {
  const t = useTranslations("jobs.scheduled");
  const fmt = useFormatters();

  const columns: Column<AstroliftScheduledJobRun>[] = [
    {
      id: "run",
      header: t("columns.app"),
      cellClassName: "max-w-80",
      cell: (j) => (
        <span className="block min-w-0">
          <span className="block truncate font-mono text-sm font-medium" title={j.workloadSlug}>
            {j.workloadSlug}
          </span>
          <span className="text-muted-foreground block truncate text-xs">
            {j.registeredAppSlug} · <span className="font-mono">{j.environmentName}</span>
          </span>
        </span>
      ),
    },
    {
      id: "k8sJob",
      header: t("columns.k8sJob"),
      cellClassName: "max-w-56",
      cell: (j) => (
        <span className="block truncate font-mono text-xs" title={j.k8sJobName}>
          {j.k8sJobName || "—"}
        </span>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (j) => <RunStatusBadge status={jobRunStatus(j)} exitCode={j.exitCode} />,
    },
    {
      id: "duration",
      header: t("columns.duration"),
      cell: (j) => (
        <span className="font-mono text-xs tabular-nums">{formatDuration(j.durationSeconds)}</span>
      ),
    },
    {
      id: "started",
      header: t("columns.started"),
      cell: (j) => (
        <span className="text-muted-foreground font-mono text-xs">
          {j.startedAt ? fmt.formatDateTime(j.startedAt) : "—"}
        </span>
      ),
    },
  ];

  return (
    <ListPage<AstroliftScheduledJobRun>
      header={{ crumbs: appsListCrumbs("jobs", { label: "Runs" }), title: "Job runs" }}
      list={list}
      label="Job runs"
      columns={columns}
      rows={rows}
      getRowId={(j) => j.id}
      rowHref={(j) => `/jobs/runs/${j.id}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <HistoryIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
        learnMoreHref:
          "https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs",
        learnMoreLabel: t("emptyLearnMore"),
      }}
      totalCount={totalCount}
      nextCursor={nextCursor}
      newRows={newRows}
    />
  );
}
