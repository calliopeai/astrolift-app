"use client";

import { ImportSkillsSheet } from "@/components/screens/agents/skills/ImportSkillsSheet";
import { SkillsListScreen } from "@/components/screens/agents/skills/SkillsListScreen";
import { useImportSkills } from "@/components/screens/agents/skills/use-import-skills";
import { useSkillsList } from "@/components/screens/agents/skills/use-skills-list";

export default function AgentSkillsPage() {
  const skills = useSkillsList();
  const importer = useImportSkills();
  return (
    <SkillsListScreen
      {...skills}
      importSheet={
        <ImportSkillsSheet
          {...importer}
          open={skills.importOpen}
          onOpenChange={skills.onImportOpenChange}
        />
      }
    />
  );
}
