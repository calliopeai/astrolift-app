"use client";

import {
  AgentSkillsList,
  AgentToolsList,
} from "@/components/screens/agents/detail/AgentSkillsTools";
import {
  useAgentSkills,
  useAgentTools,
} from "@/components/screens/agents/detail/use-agent-skills-tools";

import { useFramedAgent } from "./framed-agent";

/** Skills & tools › Skills. */
export function SkillsContent() {
  const { agent, orgId } = useFramedAgent();
  return <AgentSkillsList {...useAgentSkills(agent.slug, orgId)} />;
}

/** Skills & tools › Tools. */
export function ToolsContent() {
  const { agent, orgId } = useFramedAgent();
  return <AgentToolsList {...useAgentTools(agent.slug, orgId)} />;
}
