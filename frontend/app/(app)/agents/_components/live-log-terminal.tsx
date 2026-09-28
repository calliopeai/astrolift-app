"use client";

import { LiveLogTerminal } from "@/components/observability/LiveLogTerminal";
import { useAgentTaskLogs } from "@/components/observability/use-agent-task-logs";

/** A run's live log tail; mounted only while shown, so it polls only then. */
export function LiveLogTerminalContainer({
  taskId,
  running,
  tail,
  className,
}: {
  taskId: string;
  running: boolean;
  tail?: number;
  className?: string;
}) {
  return (
    <LiveLogTerminal
      taskId={taskId}
      running={running}
      className={className}
      {...useAgentTaskLogs(taskId, running, tail)}
    />
  );
}
