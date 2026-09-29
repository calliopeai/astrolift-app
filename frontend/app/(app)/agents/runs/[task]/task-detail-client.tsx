"use client";

import { AgentRunDetail } from "@/components/screens/agents/runs/AgentRunDetail";
import { useAgentInteractionMap } from "@/components/screens/agents/runs/use-agent-interaction-map";
import { useAgentRunDetail } from "@/components/screens/agents/runs/use-agent-run-detail";

/**
 * Agent run detail (#1105): one AgentTask on the run page. The interactions
 * are read once here and feed both the Timeline and the interaction map;
 * the log pane reads the detail hook's own log query.
 */
export function AgentTaskDetail({ taskId }: { taskId: string }) {
  const detail = useAgentRunDetail(taskId);
  const interactions = useAgentInteractionMap(taskId, detail.task?.status ?? "");
  return <AgentRunDetail {...detail} interactions={interactions} />;
}
