"use client";

import { useState } from "react";
import { toast } from "sonner";
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
import { useConfirm } from "@/hooks/use-confirm";
import {
  useCancelWorkflowInstance,
  useTerminateWorkflowInstance,
  useWorkflowInstanceDetail,
  useWorkflowInstances,
} from "@/graphql/workflows/workflows.hooks";
import type {
  WorkflowHistoryEvent,
  WorkflowInstance,
} from "@/graphql/workflows/workflows.types";

const STATUS_OPTIONS = [
  { value: "ALL", label: "All statuses" },
  { value: "RUNNING", label: "Running" },
  { value: "COMPLETED", label: "Completed" },
  { value: "FAILED", label: "Failed" },
  { value: "CANCELED", label: "Cancelled" },
  { value: "TERMINATED", label: "Terminated" },
  { value: "TIMED_OUT", label: "Timed out" },
];

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
export function WorkflowInstancesPanel({
  workflowType,
  isAdmin = true,
}: {
  workflowType?: string;
  isAdmin?: boolean;
}) {
  const [typeFilter, setTypeFilter] = useState(workflowType ?? "");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);

  const { instances, loading, error, refetch } = useWorkflowInstances({
    workflowType: typeFilter.trim() || null,
    status: statusFilter === "ALL" ? null : statusFilter,
    limit: 50,
  });

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <SearchIcon className="text-muted-foreground absolute left-2 top-1/2 size-4 -translate-y-1/2" />
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
            No workflow instances match the current filter. Temporal may be
            disabled, or the workflow type / status combination has no
            history yet.
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
                    <span className="truncate font-mono text-xs">
                      {inst.workflowId}
                    </span>
                    <StatusBadge status={inst.status} />
                  </div>
                  <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                    <span>{inst.workflowType}</span>
                    {inst.startedAt && (
                      <span>Started {formatTimestamp(inst.startedAt)}</span>
                    )}
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

      <InstanceDetailPanel
        workflowId={selectedWorkflowId}
        isAdmin={isAdmin}
        onClose={() => setSelectedWorkflowId(null)}
        onAfterMutation={() => refetch()}
      />
    </div>
  );
}

function InstanceDetailPanel({
  workflowId,
  isAdmin,
  onClose,
  onAfterMutation,
}: {
  workflowId: string | null;
  isAdmin: boolean;
  onClose: () => void;
  onAfterMutation: () => void;
}) {
  const { detail, loading, error, refetch } = useWorkflowInstanceDetail(workflowId);

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
                <span>Started {formatTimestamp(detail.instance.startedAt)}</span>
              )}
              {detail.instance.durationSeconds != null && (
                <span>{formatDuration(detail.instance.durationSeconds)}</span>
              )}
              {detail.instance.triggeredBy && (
                <span>by {detail.instance.triggeredBy}</span>
              )}
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
          {isAdmin && detail.instance.status === "RUNNING" && (
            <AdminControls
              workflowId={workflowId}
              onAfter={() => {
                refetch();
                onAfterMutation();
              }}
            />
          )}

          <ActivityFeed history={detail.history} />
        </>
      )}
    </div>
  );
}

function AdminControls({
  workflowId,
  onAfter,
}: {
  workflowId: string;
  onAfter: () => void;
}) {
  const confirm = useConfirm();
  const [cancelMutation, { loading: cancelLoading }] = useCancelWorkflowInstance();
  const [terminateMutation, { loading: terminateLoading }] =
    useTerminateWorkflowInstance();

  const handleCancel = async () => {
    const ok = await confirm({
      title: "Cancel workflow?",
      description:
        "Sends a cooperative cancel signal. The workflow may run cleanup before exiting.",
      confirmLabel: "Cancel workflow",
      cancelLabel: "Keep running",
    });
    if (!ok) return;
    const { data } = await cancelMutation({ variables: { workflowId } });
    if (data?.cancelWorkflowInstance?.ok) {
      toast.success("Cancel signal sent");
      onAfter();
    } else {
      const msg = data?.cancelWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Cancel failed";
      toast.error(msg);
    }
  };

  const handleTerminate = async () => {
    const ok = await confirm({
      title: "Terminate workflow?",
      description:
        "Hard kill — no cleanup runs. Reserve for wedged workflows the cooperative cancel can't unstick.",
      confirmLabel: "Terminate",
      cancelLabel: "Keep running",
    });
    if (!ok) return;
    const reason = window.prompt("Termination reason (required):");
    if (!reason || !reason.trim()) {
      toast.error("Termination reason is required");
      return;
    }
    const { data } = await terminateMutation({
      variables: { workflowId, reason: reason.trim() },
    });
    if (data?.terminateWorkflowInstance?.ok) {
      toast.success("Workflow terminated");
      onAfter();
    } else {
      const msg =
        data?.terminateWorkflowInstance?.errors?.[0]?.messages?.[0] ?? "Terminate failed";
      toast.error(msg);
    }
  };

  return (
    <div className="flex items-center gap-2 border-b pb-3">
      <Button
        size="sm"
        variant="outline"
        onClick={handleCancel}
        disabled={cancelLoading}
      >
        <CircleSlashIcon className="mr-1 size-3" />
        Cancel
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="text-destructive hover:text-destructive hover:bg-destructive/10"
        onClick={handleTerminate}
        disabled={terminateLoading}
      >
        <OctagonXIcon className="mr-1 size-3" />
        Terminate
      </Button>
    </div>
  );
}

function ActivityFeed({ history }: { history: WorkflowHistoryEvent[] }) {
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
                {ev.retryCount > 1 && (
                  <Badge variant="outline">retry {ev.retryCount}</Badge>
                )}
              </div>
              {Object.keys(ev.payload).length > 0 && (
                <pre className="text-muted-foreground bg-muted/40 mt-1 max-h-32 overflow-auto rounded p-2 text-[10px]">
                  {JSON.stringify(ev.payload, null, 2)}
                </pre>
              )}
            </div>
            {ev.timestamp && (
              <span className="text-muted-foreground shrink-0 whitespace-nowrap font-mono">
                {formatTimestamp(ev.timestamp)}
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

function formatTimestamp(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// Re-export the inner instance type so the parent page can compose
// without a circular dependency on the graphql module.
export type { WorkflowInstance };
