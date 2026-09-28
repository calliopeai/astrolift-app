"use client";

import { AgentRunDetail } from "@/components/screens/agents/runs/AgentRunDetail";
import { useAgentRunDetail } from "@/components/screens/agents/runs/use-agent-run-detail";

import { LiveLogTerminalContainer } from "../../_components/live-log-terminal";

import { AgentInteractionMap } from "./agent-interaction-map";

/**
 * Agent run detail (#1105) — one AgentTask by id. The screen owns the markup;
 * the interaction map and log tail are containers that poll only while shown.
 */
export function AgentTaskDetail({ taskId }: { taskId: string }) {
  const detail = useAgentRunDetail(taskId);
  const task = detail.task;
  const running = task?.status === "running";

  return (
    <AgentRunDetail
      {...detail}
      interactionMap={
        task ? <AgentInteractionMap taskId={task.id} taskStatus={task.status} /> : undefined
      }
      logTerminal={
        <LiveLogTerminalContainer
          taskId={taskId}
          running={running}
          tail={200}
          className="h-[28rem]"
        />
      }
    />
  );
}
