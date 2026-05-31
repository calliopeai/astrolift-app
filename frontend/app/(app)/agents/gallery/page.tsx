"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BotIcon, ClipboardCopyIcon, Loader2Icon, RefreshCwIcon } from "lucide-react";
import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

// ─── Types ───────────────────────────────────────────────────────────────────

type AgentTaskStatus =
  | "provisioning"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

type AgentTask = {
  guid: string;
  workloadName: string;
  status: AgentTaskStatus;
  startedAt: string | null;
  dispatcherName: string | null;
  agentVariant: string | null;
  workflowStageName: string | null;
};

type ActiveAgentTasksData = {
  activeAgentTasks: AgentTask[];
};

// ─── GraphQL ─────────────────────────────────────────────────────────────────

const LIST_ACTIVE_AGENT_TASKS = gql`
  query ListActiveAgentTasks {
    activeAgentTasks {
      guid
      workloadName
      status
      startedAt
      dispatcherName
      agentVariant
      workflowStageName
    }
  }
`;

// ─── Helpers ─────────────────────────────────────────────────────────────────

const STATUS_VARIANT: Record<
  AgentTaskStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  provisioning: "secondary",
  running: "default",
  completed: "outline",
  failed: "destructive",
  cancelled: "secondary",
};

function shortGuid(guid: string): string {
  return guid.slice(0, 8);
}

function elapsedLabel(startedAt: string | null): string {
  if (!startedAt) return "—";
  const elapsed = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
  if (elapsed < 60) return `${elapsed}s`;
  const mins = Math.floor(elapsed / 60);
  if (mins < 60) return `${mins}m ${elapsed % 60}s`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

// ─── Component ───────────────────────────────────────────────────────────────


export default function AgentGalleryPage() {
  const [elapsed, setElapsed] = useState(0);

  const { data, loading, error, refetch } = useQuery<ActiveAgentTasksData>(
    LIST_ACTIVE_AGENT_TASKS,
    {
      fetchPolicy: "cache-and-network",
      pollInterval: 5000,
    }
  );

  // Tick every second so durations re-render while the page is open.
  useEffect(() => {
    const id = setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  void elapsed; // force re-render dependency

  const tasks = data?.activeAgentTasks ?? [];

  function copyGuid(guid: string) {
    navigator.clipboard.writeText(guid).catch(() => undefined);
  }

  return (
    <PageShell
      title="Agent gallery"
      description="Live roster of running and provisioning agent tasks across all dispatchers."
      actions={
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCwIcon className="mr-1 h-3.5 w-3.5" />
          Refresh
        </Button>
      }
    >
      {loading && tasks.length === 0 && (
        <div className="flex items-center justify-center p-12">
          <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          Error: {error.message}
        </div>
      )}

      {!loading && !error && tasks.length === 0 && (
        <EmptyState
          icon={<BotIcon className="size-5" />}
          title="No active agent tasks"
          description="Agent tasks in running or provisioning state will appear here. Dispatch an agent to get started."
          actionHref="/agents"
          actionLabel="Go to Agents"
        />
      )}

      {tasks.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tasks.map((task) => (
            <Card key={task.guid} className="flex flex-col">
              <CardContent className="flex flex-col gap-3 p-5">
                {/* Header row */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-xs font-medium">
                      {shortGuid(task.guid)}
                    </span>
                    <button
                      type="button"
                      title="Copy full task ID"
                      onClick={() => copyGuid(task.guid)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <ClipboardCopyIcon className="h-3 w-3" />
                    </button>
                  </div>
                  <Badge variant={STATUS_VARIANT[task.status]}>{task.status}</Badge>
                </div>

                {/* Workload name */}
                <p className="truncate font-semibold">{task.workloadName}</p>

                {/* Meta */}
                <div className="text-muted-foreground flex flex-col gap-1 text-xs">
                  {task.dispatcherName && (
                    <span>Dispatcher: {task.dispatcherName}</span>
                  )}
                  {task.agentVariant && (
                    <span>Variant: {task.agentVariant}</span>
                  )}
                  {task.workflowStageName && (
                    <span>Stage: {task.workflowStageName}</span>
                  )}
                  <span>
                    Running:{" "}
                    {task.status === "running"
                      ? elapsedLabel(task.startedAt)
                      : task.startedAt
                        ? new Date(task.startedAt).toLocaleString()
                        : "—"}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Navigation hint back to Agents fleet page */}
      <div className="mt-2">
        <Link
          href="/agents"
          className="text-muted-foreground hover:text-foreground text-sm underline-offset-2 hover:underline"
        >
          ← Back to Agents
        </Link>
      </div>
    </PageShell>
  );
}
