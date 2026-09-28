"use client";

import { AgentRunScreen } from "@/components/screens/agents/detail/AgentRunScreen";
import { useAgentRun } from "@/components/screens/agents/detail/use-agent-run";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { LiveLogTerminalContainer } from "../../_components/live-log-terminal";

/** Run tab: dispatches the agent and polls its executions, renders the screen. */
export function RunContent({ agent, orgId }: { agent: AstroliftAgentListItem; orgId: string }) {
  return (
    <AgentRunScreen
      {...useAgentRun(agent, orgId)}
      renderLogs={(taskId, running) => (
        <LiveLogTerminalContainer taskId={taskId} running={running} className="min-h-0 flex-1" />
      )}
    />
  );
}
