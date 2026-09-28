import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ACTIVITY, DEPLOYMENTS, LONG } from "./app-detail-shell.fixtures";
import { DeployActivityStrip } from "./DeployActivityStrip";

const meta: Meta<typeof DeployActivityStrip> = {
  title: "Screens/Apps/Detail/DeployActivityStrip",
  component: DeployActivityStrip,
  args: ACTIVITY,
};
export default meta;

type Story = StoryObj<typeof DeployActivityStrip>;

/** Every status, two in flight. */
export const Full: Story = {};

export const Loading: Story = {
  args: { deployments: [], loading: true },
};

/** No deploys yet. The strip has no error state; a failed query shows this. */
export const Empty: Story = {
  args: { deployments: [] },
};

/** Long tags and environment names only reach the tile tooltips. */
export const LongStrings: Story = {
  args: {
    deployments: DEPLOYMENTS.map((d) => ({ ...d, imageTag: LONG, environmentName: LONG })),
  },
};
