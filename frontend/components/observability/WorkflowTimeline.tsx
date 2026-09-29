"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleIcon,
  ExternalLinkIcon,
  Loader2Icon,
  RefreshCwIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import * as React from "react";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import type { ActivityStatus, WorkflowActivity, WorkflowRunSummary, WorkflowStatus } from "./types";

// ─── status presentation ──────────────────────────────────────────────────────

const RUN_STATUS_VARIANT: Record<
  WorkflowStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  running: "secondary",
  completed: "default",
  failed: "destructive",
  cancelled: "outline",
  terminated: "destructive",
};

const ACTIVITY_ICON: Record<ActivityStatus, React.ComponentType<{ className?: string }>> = {
  pending: CircleIcon,
  running: Loader2Icon,
  succeeded: CheckCircle2Icon,
  failed: XCircleIcon,
  cancelled: XIcon,
};

const ACTIVITY_TONE: Record<ActivityStatus, string> = {
  pending: "text-muted-foreground",
  running: "text-warning-fg animate-spin",
  succeeded: "text-success-fg",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
};

function fmtDuration(ms: number | undefined): string {
  if (ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return `${m}m${rs}s`;
}

// ─── component ────────────────────────────────────────────────────────────────

export interface WorkflowTimelineProps {
  run: WorkflowRunSummary;
  activities: WorkflowActivity[];
  /** Cancel the in-flight workflow. Hidden if not provided or run is terminal. */
  onCancel?: () => void | Promise<void>;
  /** Retry a failed activity by id. Hidden if not provided. */
  onRetryActivity?: (activityId: string) => void | Promise<void>;
  /** Pending action — disables operator buttons while a mutation is in-flight. */
  busy?: boolean;
  /** The caller's cursor, for a long history: older activities load near the end. */
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  /** Height class for the activity frame; defaults to the Feed's. */
  maxHeight?: string;
  className?: string;
}

/**
 * Workflow run viewer — header + activity timeline. Spec 06 §8.
 *
 * Activities render as a timeline with status icons in a Feed (list rule
 * 5): the list scrolls in its own frame and, when the caller pages its
 * history, loads older activities as the reader nears the end. Failed
 * activities expand inline to show the error message and stack. Operator
 * actions (cancel run, retry activity) are wired via callbacks.
 */
export function WorkflowTimeline({
  run,
  activities,
  onCancel,
  onRetryActivity,
  busy,
  hasMore,
  loadingMore,
  onLoadMore,
  maxHeight,
  className,
}: WorkflowTimelineProps) {
  const isTerminal = run.status !== "running";

  return (
    <div className={cn("bg-card rounded-md border", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium tracking-tight">{run.workflowKind}</span>
            <Badge variant={RUN_STATUS_VARIANT[run.status]} className="capitalize">
              {run.status}
            </Badge>
          </div>
          <div className="text-muted-foreground mt-1 truncate font-mono text-xs">
            {run.workflowId} · run {run.runId.slice(0, 12)}…
          </div>
          <div className="text-muted-foreground mt-1 text-xs">
            started {new Date(run.startedAt).toLocaleString()}
            {run.endedAt && ` · ended ${new Date(run.endedAt).toLocaleString()}`}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {run.temporalUiUrl && (
            <Button asChild variant="outline" size="sm">
              <a href={run.temporalUiUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-3.5" />
                Temporal
              </a>
            </Button>
          )}
          {!isTerminal && onCancel && (
            <Button variant="ghost" size="sm" onClick={() => onCancel()} disabled={busy}>
              <XIcon className="size-3.5" />
              Cancel
            </Button>
          )}
        </div>
      </div>

      {run.status === "failed" && run.errorMessage && (
        <div className="bg-destructive/5 border-b p-4">
          <div className="flex items-start gap-2 text-sm">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="min-w-0 flex-1">
              <p className="text-destructive font-medium">Workflow failed</p>
              <p className="text-muted-foreground mt-1 break-words">{run.errorMessage}</p>
              {run.errorStack && (
                <pre className="bg-muted/40 text-2xs text-muted-foreground mt-2 overflow-x-auto rounded p-2 font-mono leading-relaxed">
                  {run.errorStack}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}

      <Feed<WorkflowActivity>
        label="Activities"
        items={activities}
        keyOf={(a) => a.id}
        hasMore={hasMore}
        loadingMore={loadingMore}
        onLoadMore={onLoadMore}
        maxHeight={maxHeight}
        dense
        className="px-2"
        empty={{ icon: <CircleIcon className="size-5" />, title: "No activities recorded yet." }}
        renderItem={(activity) => (
          <ActivityRow activity={activity} onRetry={onRetryActivity} busy={busy} />
        )}
      />
    </div>
  );
}

function ActivityRow({
  activity,
  onRetry,
  busy,
}: {
  activity: WorkflowActivity;
  onRetry?: (activityId: string) => void | Promise<void>;
  busy?: boolean;
}) {
  const [expanded, setExpanded] = React.useState(activity.status === "failed");
  const Icon = ACTIVITY_ICON[activity.status];
  const lastFailedAttempt = activity.attempts.findLast?.((a) => a.status === "failed");
  const retryCount = Math.max(0, activity.attempts.length - 1);
  const expandable = activity.status === "failed" || activity.attempts.length > 1;

  return (
    <div className="min-w-0 px-2">
      <div className="flex min-w-0 items-start gap-3">
        <div className="mt-0.5">
          <Icon className={cn("size-4", ACTIVITY_TONE[activity.status])} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm">{activity.name}</span>
            {retryCount > 0 && (
              <Badge variant="outline" className="text-2xs">
                {retryCount} retr{retryCount === 1 ? "y" : "ies"}
              </Badge>
            )}
            <span className="text-muted-foreground text-xs">
              {fmtDuration(activity.durationMs)}
            </span>
          </div>
          {expandable && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-muted-foreground hover:text-foreground mt-1 flex items-center gap-1 text-xs"
            >
              {expanded ? (
                <ChevronDownIcon className="size-3" />
              ) : (
                <ChevronRightIcon className="size-3" />
              )}
              {expanded ? "Hide attempts" : `${activity.attempts.length} attempts`}
            </button>
          )}
          {expanded && (
            <div className="mt-2 space-y-2">
              <ul className="space-y-1">
                {activity.attempts.map((attempt) => (
                  <li
                    key={attempt.attemptNumber}
                    className="text-muted-foreground flex items-center gap-2 text-xs"
                  >
                    <span className="font-mono">#{attempt.attemptNumber}</span>
                    <span className="capitalize">{attempt.status}</span>
                    <Separator
                      orientation="vertical"
                      className="h-3 data-vertical:h-3 data-vertical:self-auto"
                    />
                    <span>{new Date(attempt.startedAt).toLocaleTimeString()}</span>
                    {attempt.errorMessage && (
                      <span className="text-destructive truncate">{attempt.errorMessage}</span>
                    )}
                  </li>
                ))}
              </ul>
              {lastFailedAttempt?.errorStack && (
                <pre className="bg-muted/40 text-2xs text-muted-foreground overflow-x-auto rounded p-2 font-mono leading-relaxed">
                  {lastFailedAttempt.errorStack}
                </pre>
              )}
            </div>
          )}
        </div>
        {activity.status === "failed" && onRetry && (
          <Button variant="ghost" size="sm" onClick={() => onRetry(activity.id)} disabled={busy}>
            <RefreshCwIcon className="size-3.5" />
            Retry
          </Button>
        )}
      </div>
    </div>
  );
}
