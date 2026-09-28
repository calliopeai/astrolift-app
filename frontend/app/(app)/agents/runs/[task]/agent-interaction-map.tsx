"use client";

import { AgentInteractionMapView } from "@/components/screens/agents/runs/AgentInteractionMap";
import { useAgentInteractionMap } from "@/components/screens/agents/runs/use-agent-interaction-map";

/** Per-agent-task interaction map (#1092); polls only while mounted. */
export function AgentInteractionMap({
  taskId,
  taskStatus,
}: {
  taskId: string;
  taskStatus: string;
}) {
  return <AgentInteractionMapView {...useAgentInteractionMap(taskId, taskStatus)} />;
}
