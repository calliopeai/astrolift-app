"use client";

/**
 * One Temporal instance (#437), opened from the Platform instances list:
 * its header, the admin cancel and terminate controls, and its activity
 * feed (per-event payload and retries). The list itself is
 * WorkflowInstancesScreen, on ListPage.
 */

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { CircleSlashIcon, Loader2Icon, OctagonXIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { WorkflowHistoryEvent } from "@/graphql/workflows/workflows.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type {
  useInstanceAdminControls,
  useWorkflowInstanceDetailPanel,
} from "./use-workflow-instances";
import { formatInstanceDuration } from "./workflow-instances-list";

export type InstanceDetailViewProps = Omit<
  ReturnType<typeof useWorkflowInstanceDetailPanel>,
  "refetch"
> & {
  isAdmin: boolean;
  onClose: () => void;
  onRefresh?: () => void;
  /** Cancel / terminate controls; shown to admins while the instance is RUNNING. */
  adminControls?: ReactNode;
};

/** The selected instance: header, admin controls, and its activity feed. */
export function InstanceDetailView({
  workflowId,
  detail,
  loading,
  error,
  isAdmin,
  onClose,
  onRefresh,
  adminControls,
}: InstanceDetailViewProps) {
  const fmt = useFormatters();
  const t = useTranslations("shared.versionMismatch");

  if (!workflowId) {
    return (
      <div className="text-muted-foreground hidden items-center justify-center rounded-md border border-dashed p-6 text-center text-sm md:flex">
        Select an instance to view its activity feed.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-mono text-xs">{workflowId}</span>
            {detail && <InstanceStatusBadge status={detail.instance.status} />}
          </div>
          {detail && (
            <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 text-xs">
              <span>{detail.instance.workflowType}</span>
              {detail.instance.runId && (
                <span className="font-mono">{detail.instance.runId.slice(0, 8)}</span>
              )}
              {detail.instance.startedAt && (
                <span>Started {fmt.formatDateTime(detail.instance.startedAt)}</span>
              )}
              {detail.instance.durationSeconds != null && (
                <span>{formatInstanceDuration(detail.instance.durationSeconds)}</span>
              )}
              {detail.instance.triggeredBy && <span>by {detail.instance.triggeredBy}</span>}
            </div>
          )}
        </div>
        <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
          <XIcon className="size-4" />
        </Button>
      </div>

      {loading && !detail && (
        <div className="flex items-center justify-center p-6">
          <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
        </div>
      )}
      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
          {error.message}
        </div>
      )}
      {onRefresh && (
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
          {t("refresh")}
        </Button>
      )}

      {detail && (
        <>
          {isAdmin && detail.instance.status === "RUNNING" && adminControls}

          <ActivityFeed history={detail.history} />
        </>
      )}
    </div>
  );
}

export type InstanceAdminControlsViewProps = ReturnType<typeof useInstanceAdminControls>;

/** Cancel (cooperative) and terminate (hard kill) for a running instance. */
export function InstanceAdminControlsView({
  onCancel,
  onTerminate,
  cancelLoading,
  terminateLoading,
}: InstanceAdminControlsViewProps) {
  return (
    <div className="flex items-center gap-2 border-b pb-3">
      <Button
        size="sm"
        variant="outline"
        onClick={onCancel}
        disabled={cancelLoading || terminateLoading}
      >
        <CircleSlashIcon className="mr-1 size-3" />
        Cancel
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="text-destructive hover:text-destructive hover:bg-destructive/10"
        onClick={onTerminate}
        disabled={cancelLoading || terminateLoading}
      >
        <OctagonXIcon className="mr-1 size-3" />
        Terminate
      </Button>
    </div>
  );
}

function ActivityFeed({ history }: { history: WorkflowHistoryEvent[] }) {
  const fmt = useFormatters();
  if (history.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-4 text-center text-xs">
        No history events available for this instance.
      </div>
    );
  }
  return (
    <div className="max-h-[60vh] overflow-y-auto pr-1">
      <ol className="flex flex-col gap-1">
        {history.map((ev, idx) => (
          <li
            key={`${ev.timestamp}-${idx}`}
            className="flex items-start gap-2 border-l-2 border-dotted py-1 pl-3 text-xs"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{ev.eventType}</span>
                {ev.decision && (
                  <Badge
                    variant={
                      ev.decision === "failed" || ev.decision === "timed_out"
                        ? "destructive"
                        : ev.decision === "cancelled"
                          ? "outline"
                          : "secondary"
                    }
                  >
                    {ev.decision}
                  </Badge>
                )}
                {ev.retryCount > 1 && <Badge variant="outline">retry {ev.retryCount}</Badge>}
              </div>
              {Object.keys(ev.payload).length > 0 && (
                <pre className="text-muted-foreground bg-muted/40 text-2xs mt-1 max-h-32 overflow-auto rounded p-2">
                  {JSON.stringify(ev.payload, null, 2)}
                </pre>
              )}
            </div>
            {ev.timestamp && (
              <span className="text-muted-foreground shrink-0 font-mono whitespace-nowrap">
                {fmt.formatDateTime(ev.timestamp)}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A Temporal execution status, coloured by how it ended. */
export function InstanceStatusBadge({ status }: { status: string }) {
  const variant: "default" | "secondary" | "destructive" | "outline" =
    status === "RUNNING"
      ? "default"
      : status === "COMPLETED"
        ? "secondary"
        : status === "FAILED" || status === "TIMED_OUT" || status === "TERMINATED"
          ? "destructive"
          : "outline";
  return <Badge variant={variant}>{status}</Badge>;
}
