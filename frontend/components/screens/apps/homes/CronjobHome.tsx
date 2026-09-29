"use client";

import cronstrue from "cronstrue";
import { HistoryIcon, RepeatIcon, TimerIcon } from "lucide-react";
import * as React from "react";

import { Feed } from "@/components/feed/Feed";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { nextCronRun } from "@/lib/cron";
import { formatRelativeAge } from "@/lib/format";

import { formatDuration, runStatusDot, titleCaseStatus } from "./run-status";
import type { useCronjobRuns } from "./use-cronjob-runs";

export type CronjobHomeScreenProps = ReturnType<typeof useCronjobRuns> & {
  /** The app's name; the frame above shows it. */
  name: string;
  workload: Pick<AstroliftWorkload, "schedule" | "concurrencyPolicy">;
};

/** Why the newest run failed: the last lines of its log, else its exit code. */
function failureReason(run: AstroliftScheduledJobRun): string {
  const tail = (run.logExcerpt ?? "").trim().split("\n").slice(-3).join("\n");
  if (tail) return tail;
  return run.exitCode != null ? `Exited with code ${run.exitCode}` : "No reason recorded";
}

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

/** One run in the history feed: status, when, how long, and its exit code. */
function RunLine({ run }: { run: AstroliftScheduledJobRun }) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <Badge
        variant={runStatusDot(run.status) === "error" ? "destructive" : "secondary"}
        className="gap-1.5"
      >
        <StatusDot status={runStatusDot(run.status)} />
        {titleCaseStatus(run.status)}
      </Badge>
      <span className="text-muted-foreground font-mono text-xs" title={run.startedAt ?? undefined}>
        {run.startedAt ? formatRelativeAge(run.startedAt) : "not started"}
      </span>
      <span className="text-muted-foreground font-mono text-xs tabular-nums">
        {formatDuration(run.durationSeconds)}
      </span>
      <span className="text-muted-foreground ml-auto font-mono text-xs">
        {run.exitCode == null ? "no exit code" : `exit ${run.exitCode}`}
      </span>
    </div>
  );
}

/**
 * The Overview for a **cronjob**, schedule first: the cron expression, a
 * plain-English reading and a live countdown to the next fire time, then the
 * run history as a Feed: it scrolls in its own frame, grouped by day, and
 * loads older runs on the cursor as the reader nears the end. A failed
 * newest run puts its reason in the first panel.
 */
export function CronjobHomeScreen({
  workload,
  runs,
  totalCount,
  loading,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
}: CronjobHomeScreenProps) {
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
  const latest = runs[0];

  return (
    <PanelGrid>
      <Panel
        title="Schedule"
        icon={<RepeatIcon className="size-4" />}
        span={6}
        failure={
          latest && runStatusDot(latest.status) === "error"
            ? {
                title: "Last run failed",
                reason: <span className="whitespace-pre-wrap">{failureReason(latest)}</span>,
              }
            : null
        }
      >
        <div className="min-w-0 space-y-1">
          <div className="font-mono text-lg [overflow-wrap:anywhere]">
            {schedule || "No schedule set"}
          </div>
          {schedule ? (
            <div className="text-muted-foreground text-sm">{describe(schedule)}</div>
          ) : null}
          {workload.concurrencyPolicy ? (
            <div className="text-muted-foreground pt-1 text-xs [overflow-wrap:anywhere]">
              Concurrency: <span className="font-mono">{workload.concurrencyPolicy}</span>
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel title="Next run" icon={<TimerIcon className="size-4" />} span={6}>
        {next ? (
          <div className="space-y-1">
            <div className="font-mono text-2xl font-semibold tabular-nums">
              {now != null ? formatCountdown(next.getTime() - now) : "…"}
            </div>
            <div className="text-muted-foreground text-sm" title={next.toISOString()}>
              {next.toLocaleString()}
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            {schedule ? "Couldn't compute the next run for this expression." : "No schedule set."}
          </p>
        )}
      </Panel>

      <Panel
        title="Run history"
        icon={<HistoryIcon className="size-4" />}
        actions={
          totalCount != null ? (
            <span className="text-muted-foreground font-mono text-xs tabular-nums">
              {totalCount} total
            </span>
          ) : undefined
        }
      >
        <Feed
          label="Run history"
          items={runs}
          keyOf={(r) => r.id}
          groupBy={{ day: (r) => r.startedAt ?? r.createdAt }}
          renderItem={(r) => <RunLine run={r} />}
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={{
            icon: <HistoryIcon className="size-5" />,
            title: "No runs recorded yet",
            description: "Each scheduled execution will appear here once it fires.",
          }}
          hasMore={hasMore}
          loadingMore={loadingMore}
          onLoadMore={onLoadMore}
          dense
        />
      </Panel>
    </PanelGrid>
  );
}
