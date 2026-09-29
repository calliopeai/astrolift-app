import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LONG, NEW_SKILL } from "./agent-skills.fixtures";
import { NewSkillScreen } from "./NewSkillScreen";

const meta: Meta = {
  title: "Screens/Agents/Skills/NewSkillScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Skill: Story = { render: () => <NewSkillScreen {...NEW_SKILL} /> };

export const Instructions: Story = {
  render: () => <NewSkillScreen {...NEW_SKILL} initialStep={2} />,
};

/** The active org is still resolving, so Create is disabled (the closest thing to loading). */
export const OrgLoading: Story = {
  render: () => <NewSkillScreen {...NEW_SKILL} orgReady={false} initialStep={2} />,
};

export const Creating: Story = {
  render: () => <NewSkillScreen {...NEW_SKILL} loading initialStep={2} />,
};

/** A refused create: the slug's reason beside it, on the step that holds it. */
export const FieldErrors: Story = {
  render: () => (
    <NewSkillScreen
      {...NEW_SKILL}
      initialErrors={{ slug: `A skill with slug ${LONG} already exists in this organization.` }}
    />
  ),
};

export const FormError: Story = {
  render: () => (
    <NewSkillScreen
      {...NEW_SKILL}
      initialStep={2}
      initialErrors={{ form: "The skill was not created." }}
    />
  ),
};

/** Continue with nothing filled in: the errors appear in place, not in a toast. */
export const ValidatesInPlace: Story = {
  render: () => <NewSkillScreen {...NEW_SKILL} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(await canvas.findByText("Give the skill a name.")).toBeInTheDocument();
    await expect(canvas.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
  },
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <NewSkillScreen {...NEW_SKILL} />
    </div>
  ),
};
