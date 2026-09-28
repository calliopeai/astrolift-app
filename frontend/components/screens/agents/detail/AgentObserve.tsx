"use client";

import { MonitorPlayIcon, ScrollTextIcon, TerminalIcon } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { useAgentObserve } from "./use-agent-observe";

export type AgentObserveScreenProps = ReturnType<typeof useAgentObserve> & {
  /** The org-wide live theatre (AgentTheatreContainer). */
  theatre: ReactNode;
  /** The latest run's logs (a container running useAgentTaskLogs); shown when `latest` is set. */
  logs: ReactNode;
};

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
export function AgentObserveScreen({
  tasks,
  latest,
  loading,
  theatre,
  logs,
}: AgentObserveScreenProps) {
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
        {theatre}
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
        {loading && tasks.length === 0 ? (
          <Skeleton className="h-40 w-full" />
        ) : latest ? (
          logs
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
