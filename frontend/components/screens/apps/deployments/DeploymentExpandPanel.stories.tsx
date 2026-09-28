import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DETAIL,
  DETAIL_EMPTY,
  DETAIL_ERROR,
  DETAIL_LOADING,
  DETAIL_LONG,
} from "./app-deployments-logs.fixtures";
import { DeploymentExpandPanelView } from "./DeploymentExpandPanel";

const meta: Meta = { title: "Screens/Apps/Deployments/DeploymentExpandPanel" };
export default meta;

type Story = StoryObj;

/** A successful deploy with its manifests by kind, log and commit metadata. */
export const Full: Story = { render: () => <DeploymentExpandPanelView {...DETAIL} /> };

export const Loading: Story = { render: () => <DeploymentExpandPanelView {...DETAIL_LOADING} /> };

/** An in-flight deploy with no manifest, no log and no commit metadata yet. */
export const Empty: Story = { render: () => <DeploymentExpandPanelView {...DETAIL_EMPTY} /> };

/** A failed deploy with build output and a manifest that could not render. */
export const RenderError: Story = {
  render: () => <DeploymentExpandPanelView {...DETAIL_ERROR} />,
};

export const LongStrings: Story = {
  render: () => <DeploymentExpandPanelView {...DETAIL_LONG} />,
};
