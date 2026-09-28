"use client";

import { useQuery } from "@apollo/client/react";
import {
  ChevronRightIcon,
  MonitorPlayIcon,
  ScrollTextIcon,
  TerminalIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";

import { AgentTheatreContainer } from "../../_components/agent-theatre";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AGENT_TASK_LOGS,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";

interface AgentTask {
  id: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  failureMessage: string | null;
}
interface AgentTasksResp {
  agentTasks: AgentTask[];
}
interface AgentTaskLogsResp {
  agentTaskLogs: string[];
}

const LOG_TAIL = 200;

/**
 * Observe tab content for an agent (spec 33 PR-9).
 *
 * Two surfaces:
 *   - Aggregate live view → the org-wide `AgentTheatre` (running, watchable
 *     agents as snapshot tiles that explode into a live VNC session).
 *   - Per-run logs → the most-recent run's tail via `agentTaskLogs`.
 *
 * The full per-agent Executions list (scoped via `agentTasks(workloadId:)`)
 * is PR-10 — not built here. `agentTaskLogs` is keyed by task id; for now we
 * read the most recent running task for the org. Empty results are expected
 * on real clusters (#891 — the log relay isn't wired through) and are
 * rendered as a graceful empty state, never an error.
 */
export function ObserveContent({ orgId }: { orgId: string }) {
  // Most-recent run → its logs. Spans every status (not just running) so a run
  // that failed at spawn — which never starts a pod and has empty logs —
  // surfaces here with its `failureMessage` instead of a blank box. Sorted by
  // `createdAt`, since a spawn-failed run has no `startedAt`. (Per-agent scoping
  // arrives with the PR-10 executions list + `agentTasks(workloadId:)`.)
  const { data: tasksData, loading: tasksLoading } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const tasks = tasksData?.agentTasks ?? [];
  const latest =
    [...tasks].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""))[0] ?? null;

  return (
    <div className="space-y-6">
      {/* Aggregate live view. */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <MonitorPlayIcon className="text-muted-foreground size-4" />
          <h2 className="text-base font-semibold">Live theatre</h2>
          <span className="text-muted-foreground text-xs">
            Running watchable agents across the org
          </span>
        </div>
        <AgentTheatreContainer />
      </section>

      {/* Per-run logs. */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <ScrollTextIcon className="text-muted-foreground size-4" />
          <h2 className="text-base font-semibold">Run logs</h2>
          <span className="text-muted-foreground text-xs">
            Most recent run · full per-agent executions land in the Run tab
          </span>
        </div>
        {tasksLoading && tasks.length === 0 ? (
          <Skeleton className="h-40 w-full" />
        ) : latest ? (
          <TaskLogs task={latest} />
        ) : (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<TerminalIcon className="size-5" />}
                title="No runs yet"
                description="No agent runs to observe yet. Dispatch a run, or open the Run tab for the full execution history."
              />
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}

function TaskLogs({ task }: { task: AgentTask }) {
  const { data, loading } = useQuery<AgentTaskLogsResp>(AGENT_TASK_LOGS, {
    variables: { id: task.id, tail: LOG_TAIL },
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const lines = data?.agentTaskLogs ?? [];
  // Only set when the run failed; a spawn failure never starts a pod, so its
  // logs are empty and this is the sole debug signal.
  const failureMessage = task.failureMessage?.trim() ? task.failureMessage : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <TerminalIcon className="size-4" />
          <Link
            href={`/agents/runs/${encodeURIComponent(task.id)}`}
            className="font-mono text-xs hover:text-[var(--brand-primary)] hover:underline"
          >
            {task.id}
          </Link>
          <Badge variant="default" className="capitalize">
            {task.status}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {loading && lines.length === 0 ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : lines.length === 0 && failureMessage ? (
          // Spawn/dispatch failure: no pod ever started, so there are no logs —
          // surface the failure reason (the only debug signal) and link through
          // to the full run detail rather than showing a blank box.
          <div className="space-y-3 p-6">
            <div className="border-danger-border bg-danger/10 rounded-md border p-4">
              <div className="text-danger-fg flex items-center gap-2 text-sm font-medium">
                <XCircleIcon className="size-4" />
                Run failed before a pod started
              </div>
              <pre className="text-danger-fg mt-2 max-h-40 overflow-auto font-mono text-xs break-words whitespace-pre-wrap">
                {failureMessage}
              </pre>
            </div>
            <Button asChild variant="outline" size="sm">
              <Link href={`/agents/runs/${encodeURIComponent(task.id)}`}>
                View run detail
                <ChevronRightIcon className="size-4" />
              </Link>
            </Button>
          </div>
        ) : lines.length === 0 ? (
          // #891: agentTaskLogs returns [] on real clusters today. Render a
          // calm empty state — the run is fine, the log relay just isn't
          // delivering lines yet.
          <div className="text-muted-foreground p-6 text-sm">
            No log lines yet. Logs stream here once the run emits output. (Live log relay is
            still being wired through on some clusters.)
          </div>
        ) : (
          <pre className="max-h-[28rem] overflow-auto p-4 font-mono text-xs leading-relaxed">
            {lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap">
                {line}
              </div>
            ))}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}
