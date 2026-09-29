"use client";

import { useParams } from "next/navigation";

import { SkillToolsScreen } from "@/components/screens/agents/skills/SkillToolsScreen";
import { useSkillTools } from "@/components/screens/agents/skills/use-skill-tools";

export default function SkillToolsPage() {
  const { id } = useParams<{ id: string }>();
  return <SkillToolsScreen {...useSkillTools(id)} />;
}
