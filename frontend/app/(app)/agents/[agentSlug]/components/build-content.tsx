"use client";

import { agentTabHref } from "@/components/screens/agents/detail/agent-tabs-model";
import { AgentBuildScreen } from "@/components/screens/agents/detail/AgentBuildScreen";
import { useAgentBuild } from "@/components/screens/agents/detail/use-agent-build";

import { useFramedAgent } from "./framed-agent";

/**
 * Configuration › Build: the agent's source, image, manifest and brief. Its
 * skills are a summary here; the full list is the Skills & tools tab.
 */
export function BuildContent() {
  const { agent, orgId } = useFramedAgent();
  return (
    <AgentBuildScreen
      {...useAgentBuild(agent, orgId)}
      skillsHref={agentTabHref(agent.slug, "skills")}
    />
  );
}
