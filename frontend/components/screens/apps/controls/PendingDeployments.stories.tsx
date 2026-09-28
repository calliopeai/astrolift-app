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
