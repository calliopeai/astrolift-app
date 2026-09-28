import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  ACTIONS,
  DEPLOY_DEPLOYING,
  DEPLOY_FAILED,
  DEPLOY_LONG,
  DEPLOY_PENDING_APPROVAL,
  DEPLOY_SUPERSEDED,
  LONG,
} from "./app-deployments-logs.fixtures";
import { DeploymentRowActionsView } from "./DeploymentRowActions";

const meta: Meta = { title: "Screens/Apps/Deployments/DeploymentRowActions" };
export default meta;

type Story = StoryObj;

/** A running deploy: redeploy and roll back. */
export const Full: Story = { render: () => <DeploymentRowActionsView {...ACTIONS} /> };

export const PendingApproval: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} deployment={DEPLOY_PENDING_APPROVAL} />,
};

export const InFlight: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} deployment={DEPLOY_DEPLOYING} />,
};

export const Failed: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} deployment={DEPLOY_FAILED} />,
};

export const Superseded: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} deployment={DEPLOY_SUPERSEDED} />,
};

/** A mutation in flight: every action disabled. The closest this has to loading. */
export const Busy: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} busy />,
};

/** No permissions renders no actions at all; the closest to empty. */
export const ReadOnly: Story = {
  render: () => (
    <DeploymentRowActionsView
      {...ACTIONS}
      deployment={DEPLOY_FAILED}
      canApprove={false}
      canDeploy={false}
      canRollback={false}
    />
  ),
};

/**
 * Failures surface in the confirm dialog (the handlers throw), so there is
 * no error state on the row itself. Long strings land in the dialog titles.
 */
export const LongStrings: Story = {
  render: () => <DeploymentRowActionsView {...ACTIONS} deployment={DEPLOY_LONG} appSlug={LONG} />,
};
