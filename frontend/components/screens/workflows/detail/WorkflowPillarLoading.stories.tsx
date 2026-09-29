import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { WorkflowPillarLoading } from "./WorkflowPillarLoading";

const meta: Meta<typeof WorkflowPillarLoading> = {
  title: "Screens/Workflows/Detail/WorkflowPillarLoading",
  component: WorkflowPillarLoading,
};
export default meta;

type Story = StoryObj<typeof WorkflowPillarLoading>;

/** The only state: a spinner while the pillar page resolves its workflow. */
export const Loading: Story = {};
