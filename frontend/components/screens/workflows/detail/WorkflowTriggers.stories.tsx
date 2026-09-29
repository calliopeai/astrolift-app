import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DISABLED_WORKFLOW, LONG_WORKFLOW, WORKFLOW } from "./workflow-detail-b.fixtures";
import { WorkflowTriggersView } from "./WorkflowTriggers";

const meta: Meta<typeof WorkflowTriggersView> = {
  title: "Screens/Workflows/Detail/WorkflowTriggers",
  component: WorkflowTriggersView,
  parameters: { layout: "padded" },
  args: { trigger: WORKFLOW, canManage: true, toggling: false, onToggle: () => {} },
};
export default meta;

type Story = StoryObj<typeof WorkflowTriggersView>;

/** A schedule, enabled. The tab has no loading or error state; the frame covers both. */
export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("0 4 * * *")).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: "Disable" })).toBeEnabled();
  },
};

export const Toggling: Story = { args: { toggling: true } };

/** Manual trigger, disabled. */
export const Disabled: Story = { args: { trigger: DISABLED_WORKFLOW } };

/** No `workflow.update`: the toggle is gone. Mutation errors surface as toasts. */
export const ReadOnly: Story = {
  args: { canManage: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: "Disable" })).toBeNull();
  },
};

/** A definition opened directly runs on demand: the empty state. */
export const Definition: Story = {
  args: { trigger: null },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Runs on demand")).toBeInTheDocument();
  },
};

export const LongStrings: Story = { args: { trigger: LONG_WORKFLOW } };

export const At768: Story = {
  args: { trigger: LONG_WORKFLOW },
  render: (args) => (
    <div style={{ width: 768 }}>
      <WorkflowTriggersView {...args} />
    </div>
  ),
};
