import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DEPLOYMENTS, LATEST, LONG } from "./app-detail-shell.fixtures";
import { LatestDeploymentRow } from "./LatestDeploymentRow";

const meta: Meta<typeof LatestDeploymentRow> = {
  title: "Screens/Apps/Detail/LatestDeploymentRow",
  component: LatestDeploymentRow,
  args: LATEST,
};
export default meta;

type Story = StoryObj<typeof LatestDeploymentRow>;

export const Full: Story = {};

export const Loading: Story = {
  args: { loading: true, latest: null },
};

/** No deploys yet. The row has no error state; a failed query shows this. */
export const Empty: Story = {
  args: { latest: null },
};

/** The freshest deploy failed. */
export const Failed: Story = {
  args: { latest: DEPLOYMENTS[4] },
};

export const LongStrings: Story = {
  args: {
    latest: { ...DEPLOYMENTS[0], environmentName: LONG, commitAuthor: LONG },
  },
};
