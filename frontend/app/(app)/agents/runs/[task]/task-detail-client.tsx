"use client";

import { useQuery } from "@apollo/client/react";
import {
  ChevronRightIcon,
  ExternalLinkIcon,
  Loader2Icon,
  MonitorPlayIcon,
  ScrollIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CollapsibleCard } from "@/components/ui/collapsible-card";
import { DefinitionList } from "@/components/ui/definition-list";
import { AGENT_TASK_LOGS, GET_AGENT_TASK } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";

interface TaskResp {
  agentTask: AstroliftAgentTask | null;
}
interface LogsResp {
  agentTaskLogs: string[];
}

// Terminal AgentTask.Status values (backend contract, mirrors agents-client):
// a successful task is "completed", not "succeeded". Reaching a terminal state
// stops the live poll.
const TERMINAL_STATUSES = ["completed", "failed", "timed_out", "cancelled"];

// Agent run vocabulary → {dot, badge-variant}. Same convention the fleet
// Registry / Run surfaces use; unknown states degrade to a muted dot.
type Dot = "ok" | "warn" | "error" | "muted" | "pending";
const RUN_STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  queued: "warn",
  pending: "warn",
  provisioning: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function StatusBadge({ status }: { status: string }) {
  const dot = RUN_STATUS_DOT[status.toLowerCase()] ?? "muted";
  return (
    <Badge variant={dot === "error" ? "destructive" : "secondary"} className="gap-1.5">
      <StatusDot status={dot} />
      {titleCase(status)}
    </Badge>
  );
}

function Timestamp({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="text-muted-foreground">—</span>;
  return <span title={iso}>{formatRelativeAge(iso)}</span>;
}

/**
 * Agent run detail (#1105) — one AgentTask, read via `agentTask(id)` and
 * `agentTaskLogs(id)`. Both are tenant-scoped server-side, so no orgId arg is
 * needed. The AgentTask read type carries status / timestamps / callback /
 * result / VNC coordinates — but NOT run-mode or trigger-payload (those live on
 * the agent Workload), so this surface shows exactly what the task exposes.
 * Polls while the run is non-terminal so a live run updates without a reload.
 */
export function AgentTaskDetail({ taskId }: { taskId: string }) {
  const {
    data: taskData,
    loading,
    error,
    stopPolling: stopTaskPoll,
  } = useQuery<TaskResp>(GET_AGENT_TASK, {
    variables: { id: taskId },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });
  const { data: logsData, stopPolling: stopLogsPoll } = useQuery<LogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail: 200 },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });

  const task = taskData?.agentTask ?? null;
  const terminal = task ? TERMINAL_STATUSES.includes(task.status) : false;

  // Once the run reaches a terminal state neither the task nor its logs will
  // change again — stop both polls.
  React.useEffect(() => {
    if (terminal) {
      stopTaskPoll();
      stopLogsPoll();
    }
  }, [terminal, stopTaskPoll, stopLogsPoll]);

  if (loading && !task) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  if (error || !task) {
    return (
      <PageShell title="Run not found">
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title={error ? "Couldn't load this run" : "Run not found"}
          description={
            error
              ? error.message
              : "This agent run may not exist, or you may not have access to it."
          }
          actionHref="/agents"
          actionLabel="Back to agents"
        />
      </PageShell>
    );
  }

  const canWatch = task.status === "running" && task.vncEnabled && Boolean(task.vncUrl);
  const running = task.status === "running";
  const logs = logsData?.agentTaskLogs ?? [];
  // The human-readable spawn/dispatch failure reason (null unless the run
  // failed). A spawn-failed run never starts a pod, so its `agentTaskLogs` is
  // empty and this is the only debug signal — surface it prominently.
  const failureMessage = task.failureMessage?.trim() ? task.failureMessage : null;

  return (
    <PageShell
      collapsibleHeader
      headerStorageKey="agent-run-detail"
      title={
        <span className="flex items-center gap-2">
          <Link href="/agents?tab=history" className="text-muted-foreground hover:text-foreground">
            Agents
          </Link>
          <ChevronRightIcon className="text-muted-foreground size-4" />
          <span>Run {taskId.slice(0, 8)}</span>
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <StatusBadge status={task.status} />
          <span className="text-muted-foreground text-xs">
            created <Timestamp iso={task.createdAt} />
          </span>
        </span>
      }
      actions={
        canWatch ? (
          <Button asChild size="sm" variant="outline">
            <Link
              href={`/agents/runs/${encodeURIComponent(taskId)}/vnc`}
              target="_blank"
              rel="noreferrer"
            >
              <MonitorPlayIcon className="size-4" />
              Watch live
              <ExternalLinkIcon className="size-3.5" />
            </Link>
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-6">
        {/* Spawn/dispatch failure callout — the debug payload for a run that
            failed before (or while) starting a pod. Rendered above everything
            so it's the first thing an operator sees on a failed run. */}
        {failureMessage && (
          <div className="border-danger-border bg-danger/10 rounded-md border p-4">
            <div className="text-danger-fg flex items-center gap-2 text-sm font-medium">
              <XCircleIcon className="size-4" />
              Run failed
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              The dispatch pipeline reported an error for this run. Full reason below.
            </p>
            <pre className="text-danger-fg border-danger-border bg-danger/5 mt-3 max-h-60 overflow-auto rounded border p-3 font-mono text-xs break-words whitespace-pre-wrap">
              {failureMessage}
            </pre>
          </div>
        )}

        <CollapsibleCard title="Overview" storageKey="agent-run-overview">
          <DefinitionList
            items={[
                { term: "Status", description: <StatusBadge status={task.status} /> },
                {
                  term: "Run ID",
                  description: <span className="font-mono text-xs break-all">{task.id}</span>,
                },
                { term: "Created", description: <Timestamp iso={task.createdAt} /> },
                { term: "Started", description: <Timestamp iso={task.startedAt} /> },
                { term: "Finished", description: <Timestamp iso={task.finishedAt} /> },
                {
                  term: "Pod",
                  description: task.podName ? (
                    <span className="font-mono text-xs break-all">{task.podName}</span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  ),
                },
                {
                  term: "Namespace",
                  description: task.namespace ? (
                    <span className="font-mono text-xs break-all">{task.namespace}</span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  ),
                },
                {
                  term: "Callback URL",
                  description: task.callbackUrl ? (
                    <span className="font-mono text-xs break-all">{task.callbackUrl}</span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  ),
                },
                {
                  term: "Live session",
                  description: task.vncEnabled ? (
                    canWatch ? (
                      <Link
                        href={`/agents/runs/${encodeURIComponent(taskId)}/vnc`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[var(--brand-primary)] inline-flex items-center gap-1 hover:underline"
                      >
                        Watch live <ExternalLinkIcon className="size-3.5" />
                      </Link>
                    ) : (
                      <Badge variant="outline">VNC-capable</Badge>
                    )
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  ),
                },
            ]}
          />
        </CollapsibleCard>

        <CollapsibleCard title="Result" storageKey="agent-run-result">
          {task.result != null ? (
            <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
              {prettyJson(task.result)}
            </pre>
          ) : (
            <p className="text-muted-foreground text-sm">
              No result yet. A terminal run records its output payload here.
            </p>
          )}
        </CollapsibleCard>

        <CollapsibleCard
          storageKey="agent-run-logs"
          title={
            <span className="flex items-center gap-2">
              <ScrollIcon className="size-4" />
              Logs
              <span className="text-muted-foreground text-xs font-normal">last 200 lines</span>
            </span>
          }
        >
          {running || logs.length > 0 ? (
            <LiveLogTerminal taskId={taskId} running={running} tail={200} className="h-[28rem]" />
          ) : failureMessage ? (
            <p className="text-muted-foreground text-sm">
              No pod logs — the run failed before a pod started. See the failure above.
            </p>
          ) : (
            <p className="text-muted-foreground text-sm">
              No logs to show. Output appears here once the run&rsquo;s pod emits it.
            </p>
          )}
        </CollapsibleCard>
      </div>
    </PageShell>
  );
}
