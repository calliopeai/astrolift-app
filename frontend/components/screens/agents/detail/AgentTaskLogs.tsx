"use client";

import { XCircleIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { LogView } from "@/components/run/LogView";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";

import { runDot, RUN_STATUS_DOT } from "./agent-runs-list";
import type { AgentTask } from "./use-agent-observe";
import type { useAgentTaskLogs } from "./use-agent-task-logs";

export type AgentTaskLogsViewProps = ReturnType<typeof useAgentTaskLogs> & {
  task: AgentTask;
};

/**
 * One run's log tail on LogView (spec 44 §5.5): it follows the end, scrolls
 * in its own pane, and downloads. A run that failed before a pod started
 * has no logs; its failure message stands in the pane, the only signal
 * there is. Pure.
 */
export function AgentTaskLogsView({
  task,
  lines,
  loading,
  error,
  onRetry,
  onDownload,
  onLoadEarlier,
  onRefresh,
  hasMore,
  loadingEarlier,
  pageError,
  liveOnly,
  windowLimited,
}: AgentTaskLogsViewProps) {
  const t = useTranslations("agentObservation.logs");
  const activity = useTranslations("agentActivity");
  const statusKey = task.status.toLowerCase();
  const failureMessage = task.failureMessage?.trim() ? task.failureMessage : null;
  const href = `/agents/runs/${encodeURIComponent(task.id)}`;

  return (
    <div className="min-w-0 space-y-2">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {liveOnly && (
          <span className="text-muted-foreground text-xs" title={t("temporary")}>
            {t("live")}
          </span>
        )}
        {windowLimited && <span className="text-muted-foreground text-xs">{t("window")}</span>}
        {hasMore && (
          <Button size="sm" variant="outline" disabled={loadingEarlier} onClick={onLoadEarlier}>
            {loadingEarlier ? t("loadingEarlier") : t("loadEarlier")}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
        {pageError && (
          <span role="alert" className="text-danger-fg text-xs [overflow-wrap:anywhere]">
            {pageError}
          </span>
        )}
      </div>
      <LogView
        title={t("runTitle", { id: task.id.slice(0, 8) })}
        lines={lines}
        loading={loading}
        error={error}
        onRetry={onRetry}
        onDownload={onDownload}
        paneClassName="h-96"
        actions={
          <>
            <span className="inline-flex min-w-0 items-center gap-1.5 text-xs">
              <StatusDot status={runDot(task.status)} />
              <span className="max-w-32 truncate" title={task.status}>
                {Object.hasOwn(RUN_STATUS_DOT, statusKey)
                  ? activity(`statuses.${statusKey}`)
                  : task.status}
              </span>
            </span>
            <Link href={href} className="text-primary text-xs font-medium hover:underline">
              {t("openRun")}
            </Link>
          </>
        }
        emptyHint={
          failureMessage ? (
            <span className="not-italic">
              <span className="text-danger-fg flex items-center gap-2 font-sans text-sm font-medium">
                <XCircleIcon className="size-4 shrink-0" aria-hidden />
                {t("spawnFailed")}
              </span>
              <span className="text-danger-fg mt-2 block [overflow-wrap:anywhere] whitespace-pre-wrap">
                {failureMessage}
              </span>
            </span>
          ) : (
            t("emptyOutput")
          )
        }
      />
    </div>
  );
}
