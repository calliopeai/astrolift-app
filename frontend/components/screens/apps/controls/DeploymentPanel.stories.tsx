import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DEPLOYMENT,
  DEPLOYMENT_FAILED,
  DEPLOYMENT_IN_PROGRESS,
  DEPLOYMENT_PANEL,
  LONG,
} from "./app-controls.fixtures";
import { DeploymentPanelView } from "./DeploymentPanel";

const meta: Meta = { title: "Screens/Apps/Controls/DeploymentPanel" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <DeploymentPanelView {...DEPLOYMENT_PANEL} /> };

export const InProgress: Story = {
  render: () => <DeploymentPanelView {...DEPLOYMENT_IN_PROGRESS} />,
};

export const Loading: Story = {
  render: () => <DeploymentPanelView {...DEPLOYMENT_PANEL} loading current={null} />,
};

export const Empty: Story = {
  render: () => <DeploymentPanelView {...DEPLOYMENT_PANEL} current={null} />,
};

/** The current deployment failed, with a last-known-good to roll back to. */
export const Failed: Story = { render: () => <DeploymentPanelView {...DEPLOYMENT_FAILED} /> };

export const FailedNoRollbackTarget: Story = {
  render: () => <DeploymentPanelView {...DEPLOYMENT_FAILED} lastGood={undefined} />,
};

export const LongStrings: Story = {
  render: () => (
    <DeploymentPanelView
      {...DEPLOYMENT_PANEL}
      current={{
        ...DEPLOYMENT,
        environmentName: `preview-${LONG}`,
        workloadSlug: LONG,
        imageTag: LONG,
      }}
    />
  ),
};
