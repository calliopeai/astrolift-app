import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { ConfigureWorkflowScreen } from "./ConfigureWorkflowScreen";
import { CONFIGURE, LONG_CONFIGURE, UNBOUND_STAGES } from "./workflows-new-builder.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/New/ConfigureWorkflowScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <ConfigureWorkflowScreen {...CONFIGURE} /> };

export const Loading: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} definition={null} loading />,
};

/** No definition with this slug, or no access to it. */
export const NotFound: Story = {
  render: () => (
    <ConfigureWorkflowScreen {...CONFIGURE} definitionSlug="no-such-template" definition={null} />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <ConfigureWorkflowScreen
      {...CONFIGURE}
      definition={null}
      error={new Error("Network error: failed to fetch")}
    />
  ),
};

export const NoPermission: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} canCreate={false} />,
};

/** The template has no stages yet (the closest real empty state). */
export const NoStages: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} orderedStages={[]} />,
};

/** An agent stage without a default agent blocks submit until one is bound. */
export const UnboundStage: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} orderedStages={UNBOUND_STAGES} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/stage 3\./)).toBeInTheDocument();
    await expect(canvas.getByRole("button", { name: /Create workflow/ })).toBeDisabled();
  },
};

export const Submitting: Story = {
  render: () => <ConfigureWorkflowScreen {...CONFIGURE} submitting />,
};

export const LongStrings: Story = {
  render: () => <ConfigureWorkflowScreen {...LONG_CONFIGURE} />,
};
