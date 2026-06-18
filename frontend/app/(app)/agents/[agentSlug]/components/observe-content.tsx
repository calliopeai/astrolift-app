"use client";

import { useQuery } from "@apollo/client/react";
import { MonitorPlayIcon, ScrollTextIcon, TerminalIcon } from "lucide-react";

import { AgentTheatre } from "@/components/observability/AgentTheatre";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AGENT_TASK_LOGS,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";

interface AgentTask {
  id: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
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
  // Most-recent running task → its logs. (Per-agent scoping arrives with the
  // PR-10 executions list + `agentTasks(workloadId:)`.)
  const { data: tasksData, loading: tasksLoading } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: "running" },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const tasks = tasksData?.agentTasks ?? [];
  const latest =
    [...tasks].sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? ""))[0] ?? null;

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
        <AgentTheatre />
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
                title="No active run"
                description="There's no agent run streaming logs right now. Dispatch a run, or open the Run tab for the full execution history."
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

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <TerminalIcon className="size-4" />
          <span className="font-mono text-xs">{task.id}</span>
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
