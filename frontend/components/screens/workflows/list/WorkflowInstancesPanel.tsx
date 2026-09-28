"use client";

import type { ReactNode } from "react";
import {
  ChevronRightIcon,
  CircleSlashIcon,
  Loader2Icon,
  OctagonXIcon,
  RefreshCwIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { WorkflowHistoryEvent } from "@/graphql/workflows/workflows.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type {
  useInstanceAdminControls,
  useWorkflowInstanceDetailPanel,
  useWorkflowInstancesPanel,
} from "./use-workflow-instances";

const STATUS_OPTIONS = [
  { value: "ALL", label: "All statuses" },
  { value: "RUNNING", label: "Running" },
  { value: "COMPLETED", label: "Completed" },
  { value: "FAILED", label: "Failed" },
  { value: "CANCELED", label: "Cancelled" },
  { value: "TERMINATED", label: "Terminated" },
  { value: "TIMED_OUT", label: "Timed out" },
];

export type WorkflowInstancesPanelViewProps = ReturnType<typeof useWorkflowInstancesPanel> & {
  /** The selected instance's detail panel (InstanceDetailView behind its hook). */
  detail: ReactNode;
};

/**
 * Drill-down panel for Temporal workflow instances (#437).
 *
 * Three layered surfaces share one mount:
 *
 *   list  → status-filtered instance rows; click to drill in
 *   row   → expand the activity feed (per-event payload + retries)
 *   admin → cancel / terminate buttons gated by the parent route
 *           since admin-only check happens on the resolver
 *
 * Filters: workflow type (substring) + status. Updates are debounced
 * to the GraphQL refetch — we keep the controlled inputs immediate.
 */
export function WorkflowInstancesPanelView({
  typeFilter,
  setTypeFilter,
  statusFilter,
  setStatusFilter,
  selectedWorkflowId,
  setSelectedWorkflowId,
  instances,
  loading,
  error,
  refetch,
  detail,
}: WorkflowInstancesPanelViewProps) {
  const fmt = useFormatters();

  // When there are no instances and the user hasn't searched/filtered,
  // show a single centered empty state instead of the awkward two-panel
  // layout with empty left and "Select an instance" instruction on right.
  const isDefaultView = !typeFilter.trim() && statusFilter === "ALL";
  if (!loading && !error && instances.length === 0 && isDefaultView) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-12 text-center text-sm">
        <p className="text-foreground mb-1 font-medium">No active Temporal workflows</p>
        <p>
          Platform workflows (deploys, provisioning, drift detection) appear here while running. The
          engine is idle — everything is up to date.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <SearchIcon className="text-muted-foreground absolute top-1/2 left-2 size-4 -translate-y-1/2" />
            <Input
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              placeholder="Workflow type"
              className="pl-8"
            />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="icon"
            onClick={() => refetch()}
            aria-label="Refresh instances"
          >
            <RefreshCwIcon className="size-4" />
          </Button>
        </div>

        {loading && instances.length === 0 && (
          <div className="flex items-center justify-center p-8">
            <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
          </div>
        )}

        {error && (
          <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-3 text-sm">
            {error.message}
          </div>
        )}

        {!loading && !error && instances.length === 0 && (
          <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
            No workflow instances match the current filter. Temporal may be disabled, or the
            workflow type / status combination has no history yet.
          </div>
        )}

        <ul className="flex flex-col gap-1">
          {instances.map((inst) => (
            <li key={`${inst.workflowId}-${inst.runId}`}>
              <button
                type="button"
                onClick={() => setSelectedWorkflowId(inst.workflowId)}
                className={`hover:bg-accent flex w-full items-center justify-between gap-2 rounded-md border p-3 text-left transition-colors ${
                  selectedWorkflowId === inst.workflowId ? "bg-accent" : ""
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-xs">{inst.workflowId}</span>
                    <StatusBadge status={inst.status} />
                  </div>
                  <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <span>{inst.workflowType}</span>
                    {inst.startedAt && <span>Started {fmt.formatDateTime(inst.startedAt)}</span>}
                    {inst.durationSeconds != null && (
                      <span>{formatDuration(inst.durationSeconds)}</span>
                    )}
                    {inst.triggeredBy && <span>by {inst.triggeredBy}</span>}
                  </div>
                </div>
                <ChevronRightIcon className="text-muted-foreground size-4 shrink-0" />
              </button>
            </li>
          ))}
        </ul>
      </div>

      {detail}
    </div>
  );
}

export type InstanceDetailViewProps = Omit<
  ReturnType<typeof useWorkflowInstanceDetailPanel>,
  "refetch"
> & {
  isAdmin: boolean;
  onClose: () => void;
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
  adminControls,
}: InstanceDetailViewProps) {
  const fmt = useFormatters();

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
            {detail && <StatusBadge status={detail.instance.status} />}
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
                <span>{formatDuration(detail.instance.durationSeconds)}</span>
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
      <Button size="sm" variant="outline" onClick={onCancel} disabled={cancelLoading}>
        <CircleSlashIcon className="mr-1 size-3" />
        Cancel
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="text-destructive hover:text-destructive hover:bg-destructive/10"
        onClick={onTerminate}
        disabled={terminateLoading}
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

function StatusBadge({ status }: { status: string }) {
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

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
