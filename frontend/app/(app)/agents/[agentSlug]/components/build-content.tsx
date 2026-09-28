"use client";

import { AgentBuildScreen } from "@/components/screens/agents/detail/AgentBuildScreen";
import { useAgentBuild } from "@/components/screens/agents/detail/use-agent-build";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

/** Build tab: fetches the agent's image, manifest, brief and skills, renders the screen. */
export function BuildContent({ agent, orgId }: { agent: AstroliftAgentListItem; orgId: string }) {
  return <AgentBuildScreen {...useAgentBuild(agent, orgId)} />;
}
