import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { DISABLED_WORKFLOW, LONG_WORKFLOW, RUN } from "./workflow-detail-b.fixtures";
import { WorkflowRunScreen } from "./WorkflowRunScreen";

const meta: Meta<typeof WorkflowRunScreen> = {
  title: "Screens/Workflows/Detail/WorkflowRunScreen",
  component: WorkflowRunScreen,
  args: RUN,
};
export default meta;

type Story = StoryObj<typeof WorkflowRunScreen>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Run now" })).toBeEnabled();
    await expect(canvas.getByText("0 4 * * *")).toBeInTheDocument();
  },
};

/** A dispatch or toggle in flight. The pillar has no loading state of its own; the shell covers it. */
export const Busy: Story = {
  args: { running: true, toggling: true },
};

/** Never run, disabled: the pillar's empty state. */
export const NeverRunDisabled: Story = {
  args: { workflow: DISABLED_WORKFLOW, latest: null },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("This workflow has never run.")).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: "Run now" })).toBeDisabled();
  },
};

/**
 * No run or manage permission: both actions hidden. Mutation errors surface
 * as toasts, so the pillar has no inline error state.
 */
export const ReadOnly: Story = {
  args: { canRun: false, canManage: false },
};

export const LongStrings: Story = {
  args: { workflow: LONG_WORKFLOW, latest: LONG_WORKFLOW.runs[0] ?? null },
};
