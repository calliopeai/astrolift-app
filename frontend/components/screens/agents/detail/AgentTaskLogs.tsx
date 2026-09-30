"use client";

import { XCircleIcon } from "lucide-react";
import Link from "next/link";

import { LogView } from "@/components/run/LogView";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";

import { runDot, titleCase } from "./agent-runs-list";
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
  const failureMessage = task.failureMessage?.trim() ? task.failureMessage : null;
  const href = `/agents/runs/${encodeURIComponent(task.id)}`;

  return (
    <div className="min-w-0 space-y-2">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {liveOnly && (
          <span
            className="text-muted-foreground text-xs"
            title="Pod output is temporary and may disappear after the pod is removed."
          >
            Live pod logs
          </span>
        )}
        {windowLimited && <span className="text-muted-foreground text-xs">Recent log window</span>}
        {hasMore && (
          <Button size="sm" variant="outline" disabled={loadingEarlier} onClick={onLoadEarlier}>
            {loadingEarlier ? "Loading earlier…" : "Load earlier"}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={onRefresh}>
          Refresh log
        </Button>
        {pageError && (
          <span role="alert" className="text-danger-fg text-xs [overflow-wrap:anywhere]">
            {pageError}
          </span>
        )}
      </div>
      <LogView
        title={`Log · run ${task.id.slice(0, 8)}`}
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
                {titleCase(task.status)}
              </span>
            </span>
            <Link href={href} className="text-primary text-xs font-medium hover:underline">
              Open run
            </Link>
          </>
        }
        emptyHint={
          failureMessage ? (
            <span className="not-italic">
              <span className="text-danger-fg flex items-center gap-2 font-sans text-sm font-medium">
                <XCircleIcon className="size-4 shrink-0" aria-hidden />
                Run failed before a pod started
              </span>
              <span className="text-danger-fg mt-2 block [overflow-wrap:anywhere] whitespace-pre-wrap">
                {failureMessage}
              </span>
            </span>
          ) : (
            "No pod output is available. The run may not have written logs yet, or its pod may have been removed."
          )
        }
      />
    </div>
  );
}
