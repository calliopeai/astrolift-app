"use client";

import { ImportSkillsScreen } from "@/components/screens/agents/skills/ImportSkillsScreen";
import { useImportSkills } from "@/components/screens/agents/skills/use-import-skills";

export default function ImportSkillsPage() {
  return <ImportSkillsScreen {...useImportSkills()} />;
}
