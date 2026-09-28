import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DETAIL,
  DETAIL_EMPTY,
  DETAIL_FAILED,
  DETAIL_LOADING,
  DETAIL_LONG,
  DETAIL_NOT_FOUND,
} from "./deployments.fixtures";
import { DeploymentDetailScreen } from "./DeploymentDetailScreen";

const meta: Meta = {
  title: "Screens/Deployments/DeploymentDetailScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** A live rollout with its flow, lifecycle, release notes, approval trail, events and manifest. */
export const Full: Story = { render: () => <DeploymentDetailScreen {...DETAIL} /> };

export const Loading: Story = { render: () => <DeploymentDetailScreen {...DETAIL_LOADING} /> };

/** Every lazy card still loading after the deployment row landed. */
export const CardsLoading: Story = {
  render: () => (
    <DeploymentDetailScreen
      {...DETAIL}
      log={[]}
      logLoading
      manifestLoading
      eventsLoading
      approvalHistoryLoading
    />
  ),
};

/** Pending approval and just queued: no log, events, trail, notes or manifest yet. */
export const Empty: Story = { render: () => <DeploymentDetailScreen {...DETAIL_EMPTY} /> };

/** No deployment with this id, or no permission to see it. */
export const NotFound: Story = { render: () => <DeploymentDetailScreen {...DETAIL_NOT_FOUND} /> };

/** A failed rollout whose manifest also failed to render. */
export const Failed: Story = { render: () => <DeploymentDetailScreen {...DETAIL_FAILED} /> };

/** No permissions: no header actions. */
export const ReadOnly: Story = {
  render: () => (
    <DeploymentDetailScreen {...DETAIL} canApprove={false} canDeploy={false} canRollback={false} />
  ),
};

export const LongStrings: Story = { render: () => <DeploymentDetailScreen {...DETAIL_LONG} /> };
