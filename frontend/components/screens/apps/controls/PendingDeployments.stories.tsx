import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, PENDING, PENDING_DEPLOYMENT } from "./app-controls.fixtures";
import { PendingDeploymentsView } from "./PendingDeployments";

const meta: Meta = { title: "Screens/Apps/Controls/PendingDeployments" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PendingDeploymentsView {...PENDING} /> };

/** Viewer without app.approve_deploy. */
export const ReadOnly: Story = {
  render: () => <PendingDeploymentsView {...PENDING} canApprove={false} />,
};

/** On the Deployments tab the queue shows only when it waits on the viewer: nothing here. */
export const NotForThisViewer: Story = {
  render: () => <PendingDeploymentsView {...PENDING} canApprove={false} onlyForApprovers />,
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <PendingDeploymentsView {...PENDING} />
    </div>
  ),
};

export const Loading: Story = {
  render: () => <PendingDeploymentsView {...PENDING} loading pending={[]} />,
};

/**
 * Nothing waiting renders nothing at all, by design. The queue has no
 * error state either (failures are held in the confirm dialog), so the
 * closest shown here is a single candidate.
 */
export const SingleCandidate: Story = {
  render: () => <PendingDeploymentsView {...PENDING} pending={[PENDING_DEPLOYMENT]} />,
};

/** Builds without an image tag retain the deployment ID in the queue and confirmation. */
export const EmptyImageTag: Story = {
  render: () => (
    <PendingDeploymentsView {...PENDING} pending={[{ ...PENDING_DEPLOYMENT, imageTag: "" }]} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PendingDeploymentsView
      {...PENDING}
      pending={[
        {
          ...PENDING_DEPLOYMENT,
          imageTag: LONG,
          environmentName: `preview-${LONG}`,
          triggerKind: "promotion",
        },
      ]}
    />
  ),
};
