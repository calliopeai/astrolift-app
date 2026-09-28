import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { IMPORT_RESULT, IMPORT_SKILLS, LONG } from "./agent-skills.fixtures";
import { ImportSkillsScreen } from "./ImportSkillsScreen";

const meta: Meta = { title: "Screens/Agents/Skills/ImportSkillsScreen" };
export default meta;

type Story = StoryObj;

/** The form before an import. Import errors toast; there is no inline error state. */
export const Blank: Story = { render: () => <ImportSkillsScreen {...IMPORT_SKILLS} /> };

export const Importing: Story = {
  render: () => <ImportSkillsScreen {...IMPORT_SKILLS} loading />,
};

/** The repo's astrolift.toml declared nothing. */
export const EmptyResult: Story = {
  render: () => (
    <ImportSkillsScreen
      {...IMPORT_SKILLS}
      result={{ importedSkills: [], importedTools: [], sourceRef: "acme/empty-config@main" }}
    />
  ),
};

export const Full: Story = {
  render: () => <ImportSkillsScreen {...IMPORT_SKILLS} result={IMPORT_RESULT} />,
};

export const LongStrings: Story = {
  render: () => (
    <ImportSkillsScreen
      {...IMPORT_SKILLS}
      result={{
        importedSkills: IMPORT_RESULT.importedSkills.map((s) => `${s}-${LONG}`),
        importedTools: IMPORT_RESULT.importedTools.map((t) => `${t}_${LONG}`),
        sourceRef: `acme/${LONG}@feature/${LONG}`,
      }}
    />
  ),
};
