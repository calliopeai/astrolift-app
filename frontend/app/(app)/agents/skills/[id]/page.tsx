"use client";

import { useParams } from "next/navigation";

import { SkillBuilderScreen } from "@/components/screens/agents/skills/SkillBuilderScreen";
import { useSkillBuilder } from "@/components/screens/agents/skills/use-skill-builder";

export default function SkillBuilderPage() {
  const { id } = useParams<{ id: string }>();
  return <SkillBuilderScreen {...useSkillBuilder(id)} />;
}
