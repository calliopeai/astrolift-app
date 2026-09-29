import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FlowSteps } from "./FlowSteps";

const meta: Meta<typeof FlowSteps> = {
  title: "Screens/Agents/Skills/FlowSteps",
  component: FlowSteps,
};
export default meta;

type Story = StoryObj<typeof FlowSteps>;

export const First: Story = {
  args: { steps: [{ label: "Skill" }, { label: "Instructions" }], current: 1 },
};

export const Last: Story = {
  args: { steps: [{ label: "Where" }, { label: "Model" }, { label: "Review" }], current: 3 },
};

export const LongLabels: Story = {
  args: {
    steps: [
      { label: "Environment-and-cluster-selection-with-a-long-unbroken-label-that-wraps" },
      { label: "Model" },
      { label: "Review" },
    ],
    current: 1,
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <FlowSteps {...args} />
    </div>
  ),
};
