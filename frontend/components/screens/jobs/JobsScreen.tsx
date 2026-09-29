"use client";

import {
  CalendarClockIcon,
  HistoryIcon,
  MoreHorizontalIcon,
  PlayIcon,
  ScrollIcon,
  TerminalIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { ConcurrencyBadge } from "@/components/jobs/ConcurrencyBadge";
import { CronSchedulePreview } from "@/components/jobs/CronSchedulePreview";
import { RunStatusBadge, type RunStatus } from "@/components/jobs/RunStatusBadge";
import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { appsListCrumbs } from "@/components/screens/deployments/apps-area";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { jobRunStatus, type JobRow, jobRunsHref } from "./jobs-list";
import type { useJobs } from "./use-jobs";

export type JobsScreenProps = ReturnType<typeof useJobs>;

const MANIFEST_JOBS_DOCS =
  "https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs";

const LAST_RUN_DOT: Record<RunStatus, "ok" | "error" | "pending" | "muted"> = {
  succeeded: "ok",
  failed: "error",
  running: "pending",
  in_progress: "pending",
  superseded: "muted",
  unknown: "muted",
};

/**
 * Apps › Scheduled jobs (spec 44 §4.1, §5.1): every cron job across apps on
 * the shared list, views All · Mine · Failing · Paused, numbered pages. A
 * row opens the job's runs; Run now and the job's workload and logs sit in
 * its ⋯. On an app's Workloads tab the same list is embedded (no header,
 * views in the filter bar). Pure view; the data half is useJobs.
 */
export function JobsScreen({
  list,
  embedded,
  appSlug,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  environments,
  pendingJob,
  onRun,
}: JobsScreenProps) {
  const t = useTranslations("jobs");
  const fmt = useFormatters();

  const columns: Column<JobRow>[] = [
    {
      id: "job",
      header: "Job",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (j) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot
            status={j.lastRun ? LAST_RUN_DOT[jobRunStatus(j.lastRun)] : "muted"}
            className="mt-1.5 shrink-0"
          />
          <span className="block min-w-0">
            <span className="block truncate font-mono text-sm font-medium" title={j.slug}>
              {j.slug}
            </span>
            <span
              className="text-muted-foreground block truncate text-xs"
              title={j.registeredAppSlug}
            >
              {j.registeredAppSlug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "schedule",
      header: "Schedule",
      cell: (j) => <CronSchedulePreview schedule={j.schedule} />,
    },
    {
      id: "concurrency",
      header: "Concurrency",
      cell: (j) => <ConcurrencyBadge policy={j.concurrencyPolicy} />,
    },
    {
      id: "lastRun",
      header: "Last run",
      cell: (j) =>
        j.lastRun ? (
          <span className="flex min-w-0 flex-col gap-1">
            <RunStatusBadge status={jobRunStatus(j.lastRun)} />
            <span className="text-muted-foreground font-mono text-xs">
              {fmt.formatDateTime(j.lastRun.startedAt ?? j.lastRun.createdAt)}
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">Never run</span>
        ),
    },
  ];

  function rowActions(j: JobRow) {
    const envs = environments.filter((e) => e.registeredAppSlug === j.registeredAppSlug);
    const busy = pendingJob === `${j.registeredAppSlug}/${j.slug}`;
    return (
      <>
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          Run now in
        </DropdownMenuLabel>
        {envs.length === 0 && <DropdownMenuItem disabled>No environments</DropdownMenuItem>}
        {envs.map((e) => (
          <DropdownMenuItem key={e.id} disabled={busy} onSelect={() => void onRun(j, e.name)}>
            <PlayIcon className="size-4" />
            <span className="min-w-0 truncate font-mono">{e.name}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href={`/apps/${j.registeredAppSlug}/workloads/${j.slug}`}>
            <CalendarClockIcon className="size-4" />
            Open workload
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href={`/apps/${j.registeredAppSlug}/logs?workload=${encodeURIComponent(j.slug)}`}>
            <ScrollIcon className="size-4" />
            {t("workloadCard.openLogs")}
          </Link>
        </DropdownMenuItem>
      </>
    );
  }

  // The run lists behind the jobs: every run, or this app's, and the
  // one-off commands.
  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Run lists">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuItem asChild>
          <Link href={appSlug ? jobRunsHref(appSlug) : "/jobs/runs"}>
            <HistoryIcon className="size-4" />
            Job runs
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link
            href={appSlug ? `/jobs/commands?app=${encodeURIComponent(appSlug)}` : "/jobs/commands"}
          >
            <TerminalIcon className="size-4" />
            Command runs
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const empty: EmptyStateSpec = {
    icon: <CalendarClockIcon className="size-5" />,
    title: t("schedules.emptyTitle"),
    description: t("schedules.emptyDescription"),
    learnMoreHref: MANIFEST_JOBS_DOCS,
    learnMoreLabel: t("schedules.emptyLearnMore"),
  };

  const body = {
    list,
    label: "Scheduled jobs",
    columns,
    rows,
    getRowId: (j: JobRow) => j.id,
    rowHref: (j: JobRow) => jobRunsHref(j.registeredAppSlug, j.slug),
    rowActions,
    loading,
    stale,
    error,
    onRetry,
    empty,
    totalCount,
    menu,
  };

  return embedded ? (
    <ListPage<JobRow> embedded {...body} />
  ) : (
    <ListPage<JobRow>
      header={{ crumbs: appsListCrumbs("jobs"), title: "Scheduled jobs" }}
      {...body}
    />
  );
}
