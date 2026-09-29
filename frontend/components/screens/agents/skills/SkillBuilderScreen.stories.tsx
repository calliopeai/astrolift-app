import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { SKILL_BUILDER, SKILL_BUILDER_LONG } from "./agent-skills.fixtures";
import { SkillBuilderScreen } from "./SkillBuilderScreen";

const meta: Meta = {
  title: "Screens/Agents/Skills/SkillBuilderScreen",
  parameters: { layout: "padded" },
};
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

/** A skill with no tool definitions yet: the summary offers Register first tool. */
export const Empty: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} tools={[]} />,
};

export const ToolsLoading: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} tools={[]} toolsLoading />,
};

export const ToolsError: Story = {
  render: () => (
    <SkillBuilderScreen
      {...SKILL_BUILDER}
      tools={[]}
      toolsError={{ message: "upstream timed out" }}
    />
  ),
};

/** Details, instructions and the tools summary linking to the Tools tab. */
export const Full: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Tools" })).toHaveAttribute(
      "href",
      "/agents/skills/sk-1/tools"
    );
    await expect(canvas.getByRole("button", { name: "Save skill" })).toBeDisabled();
  },
};

/** A refused save: the reasons beside their fields. */
export const FieldErrors: Story = {
  render: () => (
    <SkillBuilderScreen
      {...SKILL_BUILDER}
      initialErrors={{ slug: "A skill with slug document-summariser already exists." }}
    />
  ),
};

export const FormError: Story = {
  render: () => (
    <SkillBuilderScreen {...SKILL_BUILDER} initialErrors={{ form: "The skill was not saved." }} />
  ),
};

export const AiAssisting: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER} aiAssisting />,
};

/** A 64-char SHA slug, a 200-char ARN description, an unbroken URL in the instructions. */
export const LongStrings: Story = {
  render: () => <SkillBuilderScreen {...SKILL_BUILDER_LONG} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <SkillBuilderScreen {...SKILL_BUILDER_LONG} />
    </div>
  ),
};
