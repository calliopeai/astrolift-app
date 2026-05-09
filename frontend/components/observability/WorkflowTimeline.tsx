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

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

import type {
  ActivityStatus,
  WorkflowActivity,
  WorkflowRunSummary,
  WorkflowStatus,
} from "./types";

// ─── status presentation ──────────────────────────────────────────────────────

const RUN_STATUS_VARIANT: Record<WorkflowStatus, "default" | "secondary" | "destructive" | "outline"> = {
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
  running: "text-amber-500 animate-spin",
  succeeded: "text-emerald-500",
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
  className?: string;
}

/**
 * Workflow run viewer — header + activity timeline. Spec 06 §8.
 *
 * Activities render as a vertical timeline with status icons; failed
 * activities expand inline to show the error message and stack.
 * Operator actions (cancel run, retry activity) are wired via callbacks.
 */
export function WorkflowTimeline({
  run,
  activities,
  onCancel,
  onRetryActivity,
  busy,
  className,
}: WorkflowTimelineProps) {
  const isTerminal = run.status !== "running";

  return (
    <div className={cn("rounded-md border bg-card", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium tracking-tight">{run.workflowKind}</span>
            <Badge variant={RUN_STATUS_VARIANT[run.status]} className="capitalize">
              {run.status}
            </Badge>
          </div>
          <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {run.workflowId} · run {run.runId.slice(0, 12)}…
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
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
        <div className="border-b bg-destructive/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <AlertTriangleIcon className="mt-0.5 size-4 text-destructive" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-destructive">Workflow failed</p>
              <p className="mt-1 break-words text-muted-foreground">{run.errorMessage}</p>
              {run.errorStack && (
                <pre className="mt-2 overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {run.errorStack}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}

      <ol className="divide-y">
        {activities.map((activity) => (
          <ActivityRow
            key={activity.id}
            activity={activity}
            onRetry={onRetryActivity}
            busy={busy}
          />
        ))}
        {activities.length === 0 && (
          <li className="p-6 text-center text-sm text-muted-foreground">
            No activities recorded yet.
          </li>
        )}
      </ol>
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
    <li className="px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          <Icon className={cn("size-4", ACTIVITY_TONE[activity.status])} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm">{activity.name}</span>
            {retryCount > 0 && (
              <Badge variant="outline" className="text-[10px]">
                {retryCount} retr{retryCount === 1 ? "y" : "ies"}
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">
              {fmtDuration(activity.durationMs)}
            </span>
          </div>
          {expandable && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
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
                    className="flex items-center gap-2 text-xs text-muted-foreground"
                  >
                    <span className="font-mono">#{attempt.attemptNumber}</span>
                    <span className="capitalize">{attempt.status}</span>
                    <Separator orientation="vertical" className="h-3 data-vertical:h-3 data-vertical:self-auto" />
                    <span>{new Date(attempt.startedAt).toLocaleTimeString()}</span>
                    {attempt.errorMessage && (
                      <span className="truncate text-destructive">{attempt.errorMessage}</span>
                    )}
                  </li>
                ))}
              </ul>
              {lastFailedAttempt?.errorStack && (
                <pre className="overflow-x-auto rounded bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {lastFailedAttempt.errorStack}
                </pre>
              )}
            </div>
          )}
        </div>
        {activity.status === "failed" && onRetry && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRetry(activity.id)}
            disabled={busy}
          >
            <RefreshCwIcon className="size-3.5" />
            Retry
          </Button>
        )}
      </div>
    </li>
  );
}
