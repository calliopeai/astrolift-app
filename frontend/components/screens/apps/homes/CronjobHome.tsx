"use client";

import cronstrue from "cronstrue";
import { HistoryIcon, RepeatIcon, TimerIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import * as React from "react";

import { useBrowserReady } from "@/hooks/use-browser-ready";

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

type HomeT = ReturnType<typeof useTranslations<"appHomes.cronjob">>;

/** Why the newest run failed: the last lines of its log, else its exit code. */
function failureReason(run: AstroliftScheduledJobRun, t: HomeT): string {
  const tail = (run.logExcerpt ?? "").trim().split("\n").slice(-3).join("\n");
  if (tail) return tail;
  return run.exitCode != null ? t("exitedWith", { code: run.exitCode }) : t("noReason");
}

function describe(schedule: string, t: HomeT): string {
  try {
    return cronstrue.toString(schedule, { verbose: true, use24HourTimeFormat: false });
  } catch {
    return t("customSchedule");
  }
}

function formatCountdown(ms: number, t: HomeT): string {
  if (ms <= 0) return t("dueNow");
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
  const t = useTranslations("appHomes.cronjob");
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
        {run.startedAt ? formatRelativeAge(run.startedAt) : t("notStarted")}
      </span>
      <span className="text-muted-foreground font-mono text-xs tabular-nums">
        {formatDuration(run.durationSeconds)}
      </span>
      <span className="text-muted-foreground ml-auto font-mono text-xs">
        {run.exitCode == null ? t("noExitCode") : t("exit", { code: run.exitCode })}
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
  const t = useTranslations("appHomes.cronjob");
  const format = useFormatter();
  const browserReady = useBrowserReady();
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const next = React.useMemo(
    () => (schedule && browserReady ? nextCronRun(schedule, new Date(now)) : null),
    [schedule, now, browserReady]
  );
  const latest = runs[0];

  return (
    <PanelGrid>
      <Panel
        title={t("schedule")}
        icon={<RepeatIcon className="size-4" />}
        span={6}
        failure={
          latest && runStatusDot(latest.status) === "error"
            ? {
                title: t("lastRunFailed"),
                reason: <span className="whitespace-pre-wrap">{failureReason(latest, t)}</span>,
              }
            : null
        }
      >
        <div className="min-w-0 space-y-1">
          <div className="font-mono text-lg [overflow-wrap:anywhere]">
            {schedule || t("noScheduleShort")}
          </div>
          {schedule ? (
            <div className="text-muted-foreground text-sm">{describe(schedule, t)}</div>
          ) : null}
          {workload.concurrencyPolicy ? (
            <div className="text-muted-foreground pt-1 text-xs [overflow-wrap:anywhere]">
              {t.rich("concurrency", {
                policy: workload.concurrencyPolicy,
                mono: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel title={t("nextRun")} icon={<TimerIcon className="size-4" />} span={6}>
        {next ? (
          <div className="space-y-1">
            <div className="font-mono text-2xl font-semibold tabular-nums">
              {now != null ? formatCountdown(next.getTime() - now, t) : "…"}
            </div>
            <div className="text-muted-foreground text-sm" title={next.toISOString()}>
              {format.dateTime(next, { dateStyle: "medium", timeStyle: "medium" })}
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            {schedule ? t("nextRunUnknown") : t("noSchedule")}
          </p>
        )}
      </Panel>

      <Panel
        title={t("runHistory")}
        icon={<HistoryIcon className="size-4" />}
        actions={
          totalCount != null ? (
            <span className="text-muted-foreground font-mono text-xs tabular-nums">
              {t("total", { count: totalCount })}
            </span>
          ) : undefined
        }
      >
        <Feed
          label={t("runHistory")}
          items={runs}
          keyOf={(r) => r.id}
          groupBy={{ day: (r) => r.startedAt ?? r.createdAt }}
          renderItem={(r) => <RunLine run={r} />}
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={{
            icon: <HistoryIcon className="size-5" />,
            title: t("emptyTitle"),
            description: t("emptyDescription"),
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
