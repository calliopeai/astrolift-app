"use client";

import { SkillsListScreen } from "@/components/screens/agents/skills/SkillsListScreen";
import { useSkillsList } from "@/components/screens/agents/skills/use-skills-list";

export default function AgentSkillsPage() {
  return <SkillsListScreen {...useSkillsList()} />;
}
