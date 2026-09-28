import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentsSubnavView } from "./AgentsSubnavView";

const meta: Meta = { title: "Screens/Agents/List/AgentsSubnavView" };
export default meta;

type Story = StoryObj;

export const OnAgents: Story = { render: () => <AgentsSubnavView pathname="/agents" /> };

export const OnSkills: Story = { render: () => <AgentsSubnavView pathname="/agents/skills" /> };

export const OnToolDetail: Story = {
  render: () => <AgentsSubnavView pathname="/agents/tools/web-search" />,
};
