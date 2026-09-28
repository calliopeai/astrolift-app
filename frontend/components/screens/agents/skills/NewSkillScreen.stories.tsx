import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { NEW_SKILL } from "./agent-skills.fixtures";
import { NewSkillScreen } from "./NewSkillScreen";

const meta: Meta = { title: "Screens/Agents/Skills/NewSkillScreen" };
export default meta;

type Story = StoryObj;

/** The blank form. Validation and server errors toast; there is no inline error state. */
export const Blank: Story = { render: () => <NewSkillScreen {...NEW_SKILL} /> };

/** The active org is still resolving, so Create is disabled (the closest thing to loading). */
export const OrgLoading: Story = {
  render: () => <NewSkillScreen {...NEW_SKILL} orgReady={false} />,
};

export const Creating: Story = { render: () => <NewSkillScreen {...NEW_SKILL} loading /> };
