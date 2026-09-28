"use client";

import { ChevronRightIcon, TerminalIcon, XCircleIcon } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { AgentTask } from "./use-agent-observe";
import type { useAgentTaskLogs } from "./use-agent-task-logs";

export type AgentTaskLogsViewProps = ReturnType<typeof useAgentTaskLogs> & {
  task: AgentTask;
};

/** One run's log tail, or its spawn failure when no pod ever started. */
export function AgentTaskLogsView({ task, lines, loading }: AgentTaskLogsViewProps) {
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
            No log lines yet. Logs stream here once the run emits output. (Live log relay is still
            being wired through on some clusters.)
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
