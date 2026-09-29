"use client";

import { NewSkillScreen } from "@/components/screens/agents/skills/NewSkillScreen";
import { useNewSkill } from "@/components/screens/agents/skills/use-new-skill";

export default function NewAgentSkillPage() {
  return <NewSkillScreen {...useNewSkill()} />;
}
