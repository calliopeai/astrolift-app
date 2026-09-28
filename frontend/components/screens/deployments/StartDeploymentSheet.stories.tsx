import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { START, START_LONG } from "./deployments.fixtures";
import { StartDeploymentSheet } from "./StartDeploymentSheet";

const meta: Meta = {
  title: "Screens/Deployments/StartDeploymentSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** An app picked: its environments listed, with approvals and paused markers. */
export const Full: Story = { render: () => <StartDeploymentSheet {...START} /> };

/** The app list has not arrived yet; the closest this sheet has to loading. */
export const Loading: Story = {
  render: () => <StartDeploymentSheet {...START} apps={[]} environments={[]} appSlug="" />,
};

/** An app with no environments: the environment picker is empty. */
export const Empty: Story = {
  render: () => <StartDeploymentSheet {...START} environments={[]} />,
};

/**
 * A failed start is reported as a toast and the sheet stays open; there is
 * no inline error state, so this shows the sheet mid-submit instead.
 */
export const Submitting: Story = {
  render: () => <StartDeploymentSheet {...START} submitting onSubmit={async () => false} />,
};

export const LongStrings: Story = { render: () => <StartDeploymentSheet {...START_LONG} /> };
