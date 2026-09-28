import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DETAIL,
  DETAIL_BUILD_FAILED,
  DETAIL_DEPLOYING,
  DETAIL_EMPTY,
  DETAIL_ERROR,
  DETAIL_FAILED,
  DETAIL_LOADING,
  DETAIL_LONG,
  DETAIL_NOT_FOUND,
} from "./deployments.fixtures";
import { DeploymentDetailScreen } from "./DeploymentDetailScreen";

const meta: Meta = {
  title: "Screens/Deployments/DeploymentDetailScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Live and healthy: every phase done, Rollback as the action, details below the run. */
export const Full: Story = { render: () => <DeploymentDetailScreen {...DETAIL} /> };

/** Mid-rollout: the rollout step pulses, the clock runs, Abort is the action. */
export const Deploying: Story = { render: () => <DeploymentDetailScreen {...DETAIL_DEPLOYING} /> };

export const Loading: Story = { render: () => <DeploymentDetailScreen {...DETAIL_LOADING} /> };

/** Every lazy panel still loading after the deployment row landed. */
export const PanelsLoading: Story = {
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

export const ErrorState: Story = { render: () => <DeploymentDetailScreen {...DETAIL_ERROR} /> };

/** The log query failed while the deployment loaded. */
export const LogError: Story = {
  render: () => (
    <DeploymentDetailScreen {...DETAIL} log={[]} logError={{ message: "upstream timed out" }} />
  ),
};

/** Health failed: the reason first, Redeploy as the action, the manifest's render error. */
export const Failed: Story = { render: () => <DeploymentDetailScreen {...DETAIL_FAILED} /> };

/** The image never built: build failed, the later phases skipped. */
export const BuildFailed: Story = {
  render: () => <DeploymentDetailScreen {...DETAIL_BUILD_FAILED} />,
};

/** No permissions: no header action, the ⋯ menu keeps the links. */
export const ReadOnly: Story = {
  render: () => (
    <DeploymentDetailScreen {...DETAIL} canApprove={false} canDeploy={false} canRollback={false} />
  ),
};

/** A 64-char SHA, a 200-char ARN in the reason and an unbroken URL in the log. */
export const LongStrings: Story = { render: () => <DeploymentDetailScreen {...DETAIL_LONG} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <DeploymentDetailScreen {...DETAIL_FAILED} />
    </div>
  ),
};
