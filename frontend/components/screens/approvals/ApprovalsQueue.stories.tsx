import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LONG, QUEUE } from "./approvals-a.fixtures";
import { ApprovalsQueueScreen } from "./ApprovalsQueue";

const meta: Meta = {
  title: "Screens/Approvals/ApprovalsQueue",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Three pending deploys across two app/environment pairs. */
export const Full: Story = {
  render: () => <ApprovalsQueueScreen {...QUEUE} />,
};

/** Selecting every row spans two environments, so the bulk CTAs disable. */
export const CrossEnvSelection: Story = {
  render: () => <ApprovalsQueueScreen {...QUEUE} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("checkbox", { name: "Select all pending" }));
    await expect(canvas.getByRole("button", { name: /Approve 3/ })).toBeDisabled();
  },
};

export const Loading: Story = {
  render: () => <ApprovalsQueueScreen {...QUEUE} pending={[]} loading />,
};

/** Nothing pending. The queue has no error state; a failed query renders this too. */
export const Empty: Story = {
  render: () => <ApprovalsQueueScreen {...QUEUE} pending={[]} />,
};

/** Viewer without app.approve_deploy: no row checkboxes, read-only hint. */
export const ReadOnly: Story = {
  render: () => <ApprovalsQueueScreen {...QUEUE} canApprove={false} />,
};

export const LongStrings: Story = {
  render: () => (
    <ApprovalsQueueScreen
      {...QUEUE}
      pending={QUEUE.pending.map((d) => ({
        ...d,
        registeredAppSlug: LONG,
        environmentName: LONG,
        imageTag: LONG,
      }))}
    />
  ),
};
