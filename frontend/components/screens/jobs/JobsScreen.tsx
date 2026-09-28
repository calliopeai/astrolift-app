"use client";

import {
  CalendarClockIcon,
  ClockIcon,
  HistoryIcon,
  Loader2Icon,
  PlayIcon,
  ScrollIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConcurrencyBadge } from "@/components/jobs/ConcurrencyBadge";
import { CronSchedulePreview } from "@/components/jobs/CronSchedulePreview";
import { RunStatusBadge, commandRunStatus } from "@/components/jobs/RunStatusBadge";
import {
  DataTable,
  type Column,
  type CursorTableController,
  type EmptyStateSpec,
} from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Section } from "@/components/ui/section";
import { MiniBar } from "@/components/viz";
import type {
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatDuration } from "./jobs-format";
import type { CronWorkload, JobRunPulse, useCronWorkloadRun, useJobs } from "./use-jobs";

export type JobsScreenProps = ReturnType<typeof useJobs> & {
  appSlug?: string;
  /** App tabs when rendered under /apps/[slug]/jobs. */
  tabs?: React.ReactNode;
  /**
   * The per-app Schedules table, a container that fetches environments
   * and runs jobs. Rendered only on the Schedules tab of an app.
   */
  cronWorkloads?: React.ReactNode;
};

// Kept out of the component body so the impure Date.now() isn't a render-phase
// call (react-hooks/purity). Display-only; recomputed each poll.
function summarizeJobRuns(runs: JobRunPulse[], window: number) {
  const perDay = new Array<number>(window).fill(0);
  const now = Date.now();
  const counts = { succeeded: 0, failed: 0, running: 0 };
  for (const r of runs) {
    const t = Date.parse(r.startedAt ?? r.createdAt);
    if (!Number.isNaN(t)) {
      const ago = Math.floor((now - t) / 86_400_000);
      if (ago >= 0 && ago < window) perDay[window - 1 - ago] += 1;
    }
    if (r.status === "succeeded") counts.succeeded += 1;
    else if (r.status === "failed") counts.failed += 1;
    else if (r.status === "running") counts.running += 1;
  }
  return { perDay, counts };
}

// At-a-glance overview above the runs table: 14-day run volume + terminal
// outcome counts, from the overview query's own window (no extra query).
function JobRunsSummary({ runs }: { runs: JobRunPulse[] }) {
  const WINDOW = 14;
  const { perDay, counts } = summarizeJobRuns(runs, WINDOW);

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-md border px-4 py-3">
      <div className="flex flex-wrap items-center gap-3 text-xs tabular-nums">
        <span className="text-success-fg font-medium">{counts.succeeded} succeeded</span>
        <span className="text-danger-fg font-medium">{counts.failed} failed</span>
        <span className="text-info-fg font-medium">{counts.running} running</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-2xs tracking-wide uppercase">
          {WINDOW}-day volume
        </span>
        <MiniBar data={perDay} width={180} height={32} className="text-chart-1" />
      </div>
    </div>
  );
}

/**
 * Jobs: schedules, runs, failures, one-off commands and a logs gateway.
 * Fleet-wide at /jobs, app-scoped at /apps/[slug]/jobs. Data comes from
 * useJobs; the per-app schedules table arrives as the `cronWorkloads` slot.
 */
export function JobsScreen({
  appSlug,
  tabs,
  cronWorkloads,
  tab,
  setTab,
  summaryRuns,
  scheduleCount,
  runCount,
  failureCount,
  commandCount,
  runsTable,
  failuresController,
  commandsTable,
  schedulesController,
}: JobsScreenProps) {
  const t = useTranslations("jobs");

  const jobsExample = <ScheduledJobsExample caption={t("scheduled.emptyExampleCaption")} />;

  return (
    <PageShell
      title={t("title")}
      description={
        appSlug ? (
          <span className="text-muted-foreground font-mono text-xs">
            {t("descriptionForApp", { app: appSlug })}
          </span>
        ) : (
          t("descriptionGlobal")
        )
      }
    >
      {tabs}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={tab === "schedules" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("schedules")}
          className="gap-2"
        >
          <CalendarClockIcon className="size-4" /> {t("tabs.schedules")}
          {appSlug ? (
            <Badge variant="secondary" className="ml-1">
              {scheduleCount ?? "—"}
            </Badge>
          ) : null}
        </Button>
        <Button
          variant={tab === "runs" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("runs")}
          className="gap-2"
        >
          <HistoryIcon className="size-4" /> {t("tabs.runs")}
          <Badge variant="secondary" className="ml-1">
            {runCount ?? "—"}
          </Badge>
        </Button>
        <Button
          variant={tab === "failures" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("failures")}
          className="gap-2"
        >
          <XCircleIcon className="size-4" /> {t("tabs.failures")}
          <Badge
            variant={failureCount !== null && failureCount > 0 ? "destructive" : "secondary"}
            className="ml-1"
          >
            {failureCount ?? "—"}
          </Badge>
        </Button>
        <Button
          variant={tab === "commands" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("commands")}
          className="gap-2"
        >
          <TerminalIcon className="size-4" /> {t("tabs.commands")}
          <Badge variant="secondary" className="ml-1">
            {commandCount ?? "—"}
          </Badge>
        </Button>
        <Button
          variant={tab === "logs" ? "default" : "outline"}
          size="sm"
          onClick={() => setTab("logs")}
          className="gap-2"
        >
          <ScrollIcon className="size-4" /> Logs
        </Button>
      </div>

      {tab === "schedules" &&
        (!appSlug ? (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<CalendarClockIcon className="size-5" />}
                title={t("schedules.emptyTitle")}
                description={t("schedules.emptyForFleet")}
              />
            </CardContent>
          </Card>
        ) : (
          <div className="flex flex-col gap-3">
            {cronWorkloads}
            {/* EmptyStateSpec carries no `secondary` slot, so the worked
                astrolift.toml example sits under the empty table. */}
            {schedulesController.state === "empty" && (
              <div className="flex justify-center">{jobsExample}</div>
            )}
          </div>
        ))}

      {tab === "runs" && (
        <div className="flex flex-col gap-3">
          {summaryRuns.length > 0 && <JobRunsSummary runs={summaryRuns} />}
          <ScheduledJobRunsTable
            label="Job runs"
            controller={runsTable}
            searchPlaceholder="Filter runs…"
            empty={{
              icon: <HistoryIcon className="size-5" />,
              title: t("scheduled.emptyTitle"),
              description: t("scheduled.emptyDescription"),
              learnMoreHref:
                "https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs",
              learnMoreLabel: t("scheduled.emptyLearnMore"),
            }}
            emptyFiltered={{
              title: "No matching runs",
              description:
                "No run matches that search. The server looks at the app, workload, environment, status, and the batch/v1 Job name.",
            }}
          />
          {runsTable.state === "empty" && <div className="flex justify-center">{jobsExample}</div>}
        </div>
      )}

      {tab === "failures" && (
        <ScheduledJobRunsTable
          label="Failed runs"
          controller={failuresController}
          empty={{
            icon: <XCircleIcon className="size-5" />,
            title: t("failures.emptyTitle"),
            description: t("failures.emptyDescription"),
          }}
        />
      )}

      {tab === "commands" && (
        <CommandRunsTable
          controller={commandsTable}
          empty={{
            icon: <TerminalIcon className="size-5" />,
            title: t("commands.emptyTitle"),
            description: t("commands.emptyDescription"),
          }}
        />
      )}

      {/* Gateway placeholder ported from /observe/jobs (#892). */}
      {tab === "logs" && (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<ScrollIcon className="size-5" />}
              title="Job Logs"
              description="Stdout/stderr from completed job runs. Each run's log tail is stored inline; full log available on demand."
            />
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}

export type CronWorkloadsTableProps = ReturnType<typeof useCronWorkloadRun> & {
  appSlug: string;
  controller: CursorTableController<CronWorkload>;
};

/**
 * Cron workloads of one app with a per-row environment pick and Run now
 * (#670). Defaults to the first env (typically 'production'); the operator
 * can switch via the per-row select to fire the job into a preview env.
 */
export function CronWorkloadsTable({
  appSlug,
  controller,
  envList,
  pendingSlug,
  onRun,
}: CronWorkloadsTableProps) {
  const t = useTranslations("jobs.workloadCard");
  const tSchedules = useTranslations("jobs.schedules");
  const defaultEnv = envList[0]?.name ?? "";
  const [perRowEnv, setPerRowEnv] = React.useState<Record<string, string>>({});

  const columns: Column<CronWorkload>[] = [
    {
      id: "workload",
      header: "Workload",
      cell: (w) => (
        <div className="flex items-center gap-2">
          <code className="font-mono text-sm font-medium">{w.slug}</code>
          <ConcurrencyBadge policy={w.concurrencyPolicy} />
        </div>
      ),
    },
    {
      id: "schedule",
      header: "Schedule",
      cell: (w) => <CronSchedulePreview schedule={w.schedule} />,
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (w) => {
        const envName = perRowEnv[w.slug] ?? defaultEnv;
        const isPending = pendingSlug === w.slug;
        return (
          <div className="flex items-center justify-end gap-2">
            {envList.length > 1 && (
              <Select
                value={envName}
                onValueChange={(v) => setPerRowEnv((prev) => ({ ...prev, [w.slug]: v }))}
              >
                <SelectTrigger className="h-8 w-32 text-xs">
                  <SelectValue placeholder="env" />
                </SelectTrigger>
                <SelectContent>
                  {envList.map((e) => (
                    <SelectItem key={e.id} value={e.name}>
                      {e.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => onRun(w.slug, envName)}
              disabled={isPending || !envName}
            >
              {isPending ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <PlayIcon className="size-4" />
              )}
              Run now
            </Button>
            <Button asChild size="sm" variant="ghost">
              <a href={`/apps/${appSlug}/logs?workload=${w.slug}`}>{t("openLogs")}</a>
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <Section title={t("title")}>
      <DataTable
        label="Cron workloads"
        controller={controller}
        columns={columns}
        getRowId={(w) => w.id}
        empty={{
          icon: <CalendarClockIcon className="size-5" />,
          title: tSchedules("emptyTitle"),
          description: tSchedules("emptyDescription"),
          learnMoreHref:
            "https://github.com/calliopeai/astrolift-docs/blob/main/reference/manifest.md#jobs",
          learnMoreLabel: tSchedules("emptyLearnMore"),
        }}
      />
    </Section>
  );
}

function ScheduledJobsExample({ caption }: { caption: string }) {
  // Concrete worked example of a `[[jobs]]` block — picked to mirror the
  // shape an operator will paste into their astrolift.toml verbatim:
  // slug, image, the cron `schedule`, and the `command` to run.
  // Kept to 5 lines so the empty state stays visually compact.
  const example = [
    "[[jobs]]",
    'slug = "nightly-report"',
    'image = "ghcr.io/acme/reports:latest"',
    'schedule = "0 2 * * *"   # 02:00 UTC daily',
    'command = ["python", "-m", "reports.nightly"]',
  ].join("\n");
  return (
    <div className="mt-2 w-full max-w-xl text-left">
      <p className="text-muted-foreground mb-2 text-xs">{caption}</p>
      <pre className="bg-muted text-muted-foreground text-2xs overflow-x-auto rounded-md border p-3 font-mono leading-relaxed">
        {example}
      </pre>
    </div>
  );
}

function ScheduledJobRunsTable({
  label,
  controller,
  empty,
  emptyFiltered,
  searchPlaceholder,
}: {
  label: string;
  controller: CursorTableController<AstroliftScheduledJobRun>;
  empty: EmptyStateSpec;
  emptyFiltered?: { title: string; description?: string };
  searchPlaceholder?: string;
}) {
  const t = useTranslations("jobs.scheduled");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined) => (iso ? fmt.formatDateTime(iso) : "—");

  // `astroliftScheduledJobRunsPage` takes `appSlug`, `environmentName`,
  // `search`, `limit` and `after` — there is no sort argument, so no
  // column declares a `sortKey`. Rows arrive newest-first from the
  // server's `(-created_at, -guid)` seek key.
  const columns: Column<AstroliftScheduledJobRun>[] = [
    {
      id: "app",
      header: t("columns.app"),
      // Spans, not divs: this is the linked cell, so its content renders
      // inside an <a> and a block-in-inline nesting is invalid there.
      cell: (j) => (
        <span className="block">
          <span className="block font-medium">{j.registeredAppSlug}</span>
          <span className="text-muted-foreground block text-xs">
            {t("envLabel")} <span className="font-mono">{j.environmentName}</span>
            {" · "}
            {t("workloadLabel")} <span className="font-mono">{j.workloadSlug}</span>
          </span>
        </span>
      ),
    },
    {
      id: "k8sJob",
      header: t("columns.k8sJob"),
      cellClassName: "font-mono text-xs",
      cell: (j) => j.k8sJobName || "—",
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (j) => <RunStatusBadge status={j.status} exitCode={j.exitCode} />,
    },
    {
      id: "duration",
      header: t("columns.duration"),
      cellClassName: "font-mono text-xs",
      cell: (j) => (
        <span className="inline-flex items-center gap-1">
          <ClockIcon className="size-3" />
          {formatDuration(j.durationSeconds)}
        </span>
      ),
    },
    {
      id: "started",
      header: t("columns.started"),
      cellClassName: "text-muted-foreground text-sm",
      cell: (j) => formatTime(j.startedAt),
    },
  ];

  return (
    <DataTable
      label={label}
      controller={controller}
      columns={columns}
      getRowId={(j) => j.id}
      rowHref={(j) => `/jobs/runs/${j.id}`}
      searchPlaceholder={searchPlaceholder}
      empty={empty}
      emptyFiltered={emptyFiltered}
    />
  );
}

function CommandRunsTable({
  controller,
  empty,
}: {
  controller: CursorTableController<AstroliftCommandRun>;
  empty: EmptyStateSpec;
}) {
  const t = useTranslations("jobs.commands");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined) => (iso ? fmt.formatDateTime(iso) : "—");

  // `astroliftCommandRunsPage` takes `appSlug`, `search`, `limit` and
  // `after` only — no sort argument, so no column declares a `sortKey`.
  const columns: Column<AstroliftCommandRun>[] = [
    {
      id: "app",
      header: t("columns.app"),
      // Spans, not divs: the linked cell's content renders inside an <a>.
      cell: (c) => (
        <span className="block">
          <span className="block font-medium">{c.registeredAppSlug}</span>
          {c.workloadSlug ? (
            <span className="text-muted-foreground block text-xs">
              {t("workloadLabel")} <span className="font-mono">{c.workloadSlug}</span>
            </span>
          ) : null}
        </span>
      ),
    },
    {
      id: "command",
      header: t("columns.command"),
      cellClassName: "font-mono text-xs",
      cell: (c) =>
        Array.isArray(c.command) ? (c.command as string[]).join(" ") : JSON.stringify(c.command),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (c) => (
        <RunStatusBadge
          status={commandRunStatus({ endedAt: c.endedAt, exitCode: c.exitCode })}
          exitCode={c.exitCode}
        />
      ),
    },
    {
      id: "invokedBy",
      header: t("columns.invokedBy"),
      cellClassName: "text-sm",
      cell: (c) => c.invokedByUsername ?? "—",
    },
    {
      id: "started",
      header: t("columns.started"),
      cellClassName: "text-muted-foreground text-sm",
      cell: (c) => formatTime(c.startedAt),
    },
  ];

  return (
    <DataTable
      label="Command runs"
      controller={controller}
      columns={columns}
      getRowId={(c) => c.id}
      rowHref={(c) => `/jobs/commands/${c.id}`}
      searchPlaceholder="Filter commands…"
      empty={empty}
      emptyFiltered={{
        title: "No matching commands",
        description:
          "No command run matches that search. The server looks at the app, workload, the operator who invoked it, and the batch/v1 Job name.",
      }}
    />
  );
}
