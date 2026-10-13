import {
  activeAgentSection,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";
import { AgentTabSections } from "@/components/screens/agents/detail/AgentTabSections";
import { routeTitle } from "@/lib/i18n/route-title";

import { SkillsContent, ToolsContent } from "../components/skills-content";

export const generateMetadata = routeTitle("skillsAndTools", "agent");

/**
 * The Skills & tools tab (spec 44 §5.2): the skills bound to the agent, and
 * the tools they carry, one list per section by `?section=`.
 */
export default async function AgentSkillsPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentSlug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { agentSlug } = await params;
  const section = activeAgentSection("skills", await searchParams);
  return (
    <AgentTabSections slug={agentSlug} tab="skills" active={section}>
      {section === "tools" ? <ToolsContent /> : <SkillsContent />}
    </AgentTabSections>
  );
}
