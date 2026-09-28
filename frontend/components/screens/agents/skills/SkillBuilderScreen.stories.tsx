import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SKILL_BUILDER, SKILL_BUILDER_LONG } from "./agent-skills.fixtures";
import { SkillBuilderScreen } from "./SkillBuilderScreen";

const meta: Meta = { title: "Screens/Agents/Skills/SkillBuilderScreen" };
export default meta;

type Story = StoryObj;

export const Loading: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} skill={null} skillLoading />,
};

/** The skill query returned nothing: deleted, or not visible to this org. */
export const NotFound: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} skill={null} />,
};

export const LoadError: Story = {
  render: () => (
    <SkillBuilderScreen
      {...SKILL_BUILDER}
      skill={null}
      errorMessage="Response not successful: Received status code 500"
    />
  ),
};

/** A skill with no tool definitions yet. */
export const Empty: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} tools={[]} />,
};

export const ToolsLoading: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} tools={[]} toolsLoading />,
};

/** One tool per adapter; one without a description, one without a handler ref. */
export const Full: Story = { render: () => <SkillBuilderScreen {...SKILL_BUILDER} /> };

export const AiAssisting: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} aiAssisting />,
};

export const LongStrings: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER_LONG} />,
};
