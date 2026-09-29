"use client";

import { AgentVncPopoutScreen } from "@/components/screens/agents/runs/AgentVncPopout";
import { useAgentVncPopout } from "@/components/screens/agents/runs/use-agent-vnc-popout";

/** Full-height single-session noVNC viewer for the pop-out tab. */
export function AgentVncPopout({ taskId }: { taskId: string }) {
  return <AgentVncPopoutScreen {...useAgentVncPopout(taskId)} />;
}
