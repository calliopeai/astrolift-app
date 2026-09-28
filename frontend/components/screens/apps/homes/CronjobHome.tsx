"use client";

import cronstrue from "cronstrue";
import { HistoryIcon, RepeatIcon, TimerIcon } from "lucide-react";
import * as React from "react";

import { DataTable, type Column } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { nextCronRun } from "@/lib/cron";
import { formatRelativeAge } from "@/lib/format";

import { formatDuration, runStatusDot, titleCaseStatus } from "./run-status";
import type { useCronjobRuns } from "./use-cronjob-runs";

export type CronjobHomeScreenProps = ReturnType<typeof useCronjobRuns> & {
  name: string;
  workload: Pick<AstroliftWorkload, "schedule" | "concurrencyPolicy">;
  /** The app's tab bar. */
  tabs?: React.ReactNode;
};

function describe(schedule: string): string {
  try {
    return cronstrue.toString(schedule, { verbose: true, use24HourTimeFormat: false });
  } catch {
    return "Custom schedule";
  }
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "due now";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

const columns: Column<AstroliftScheduledJobRun>[] = [
  {
    id: "status",
    header: "Status",
    cell: (r) => (
      <Badge
        variant={runStatusDot(r.status) === "error" ? "destructive" : "secondary"}
        className="gap-1.5"
      >
        <StatusDot status={runStatusDot(r.status)} />
        {titleCaseStatus(r.status)}
      </Badge>
    ),
  },
  {
    id: "started",
    header: "Started",
    cellClassName: "text-muted-foreground text-sm",
    cell: (r) =>
      r.startedAt ? <span title={r.startedAt}>{formatRelativeAge(r.startedAt)}</span> : "—",
  },
  {
    id: "duration",
    header: "Duration",
    cellClassName: "text-muted-foreground text-sm tabular-nums",
    cell: (r) => formatDuration(r.durationSeconds),
  },
  {
    id: "exit",
    header: "Exit",
    align: "right",
    cellClassName: "font-mono text-xs",
    cell: (r) => (r.exitCode == null ? "—" : r.exitCode),
  },
];

/**
 * Primitive home for a **cronjob** — schedule-first ("watch"): the cron
 * expression front and centre, a plain-English reading, and a live countdown to
 * the next fire time. Run history slots below (graceful empty state until the
 * per-run feed is wired).
 */
export function CronjobHomeScreen({ name, workload, table, tabs }: CronjobHomeScreenProps) {
  const schedule = workload.schedule || "";
  const [now, setNow] = React.useState<number | null>(null);
  React.useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const next = React.useMemo(
    () => (schedule && now != null ? nextCronRun(schedule, new Date(now)) : null),
    [schedule, now]
  );

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <TimerIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <TimerIcon className="size-3" />
            Scheduled job
          </Badge>
        </span>
      }
      description={
        <span className="text-muted-foreground text-xs">
          {schedule ? describe(schedule) : "No schedule set"}
        </span>
      }
    >
      {tabs}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <RepeatIcon className="size-4" />
              Schedule
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-6 sm:grid-cols-2">
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs tracking-wide uppercase">Cron</div>
              <div className="font-mono text-lg">{schedule || "—"}</div>
              {schedule ? (
                <div className="text-muted-foreground text-sm">{describe(schedule)}</div>
              ) : null}
              {workload.concurrencyPolicy ? (
                <div className="text-muted-foreground pt-1 text-xs">
                  Concurrency: <span className="font-medium">{workload.concurrencyPolicy}</span>
                </div>
              ) : null}
            </div>
            <div className="space-y-1">
              <div className="text-muted-foreground text-xs tracking-wide uppercase">Next run</div>
              {next ? (
                <>
                  <div className="text-2xl font-semibold tabular-nums">
                    {now != null ? formatCountdown(next.getTime() - now) : "—"}
                  </div>
                  <div className="text-muted-foreground text-sm" title={next.toISOString()}>
                    {next.toLocaleString()}
                  </div>
                </>
              ) : (
                <div className="text-muted-foreground text-sm">
                  {schedule ? "Couldn't compute the next run for this expression." : "—"}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <HistoryIcon className="size-4" />
              Run history
              {table.totalCount != null && (
                <span className="text-muted-foreground text-xs font-normal tabular-nums">
                  {table.totalCount} total
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <DataTable
              label="Run history"
              controller={table}
              columns={columns}
              getRowId={(r) => r.id}
              searchPlaceholder="Search runs by status or Job name…"
              empty={{
                icon: <HistoryIcon className="size-5" />,
                title: "No runs recorded yet",
                description: "Each scheduled execution will appear here once it fires.",
              }}
              emptyFiltered={{
                title: "No matching runs",
                description:
                  "No run of this job matches that search. The server matches the status and the batch/v1 Job name.",
              }}
            />
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
