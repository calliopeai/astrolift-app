"use client";

import { AgentOverviewView } from "@/components/screens/agents/detail/AgentOverview";
import { useAgentOverview } from "@/components/screens/agents/detail/use-agent-overview";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

/**
 * The agent Overview pillar, wired to its data. The view lives in
 * components/screens/agents/detail/AgentOverview.
 */
export function OverviewContent({
  agent,
  orgId,
}: {
  agent: AstroliftAgentListItem;
  orgId: string;
}) {
  return <AgentOverviewView {...useAgentOverview({ agent, orgId })} />;
}
