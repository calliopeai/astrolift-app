import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DEPLOY_TOKEN, LONG, TOKEN } from "./app-controls.fixtures";
import { DeployTokenControlView } from "./DeployTokenControl";

const meta: Meta = { title: "Screens/Apps/Controls/DeployTokenControl" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <DeployTokenControlView {...DEPLOY_TOKEN} /> };

export const NeverUsed: Story = {
  render: () => (
    <DeployTokenControlView {...DEPLOY_TOKEN} active={{ ...TOKEN, lastUsedAt: null }} />
  ),
};

export const Loading: Story = {
  render: () => <DeployTokenControlView {...DEPLOY_TOKEN} loading active={null} />,
};

/**
 * No active token. The card has no error state (a failed create is a
 * toast), so this warning card is also the closest to one.
 */
export const Empty: Story = {
  render: () => <DeployTokenControlView {...DEPLOY_TOKEN} active={null} />,
};

export const Busy: Story = {
  render: () => <DeployTokenControlView {...DEPLOY_TOKEN} rotating revoking />,
};

/** The one time the plaintext is shown. */
export const Revealed: Story = {
  render: () => (
    <DeployTokenControlView {...DEPLOY_TOKEN} reveal="astd_7Qm2kX9vR4pL8nB3cW6yT1hJ5fG0sD" />
  ),
};

export const LongStrings: Story = {
  render: () => <DeployTokenControlView {...DEPLOY_TOKEN} reveal={`astd_${LONG}${LONG}`} />,
};
