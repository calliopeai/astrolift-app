import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ENV_LONG, ENV_STAGING_PAUSED, ENV_UNPLACED, ENVIRONMENT } from "./environments.fixtures";
import { EnvironmentDetail } from "./EnvironmentDetail";

const meta: Meta = {
  title: "Screens/Environments/EnvironmentDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <EnvironmentDetail {...ENVIRONMENT} /> };

export const Loading: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} loading environment={null} />,
};

/** Paused deploys and ingress, and no per-environment settings. */
export const Empty: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_STAGING_PAUSED} />,
};

/** Not placed on a cluster: URL, cluster, provider and zone read as a dash. */
export const Unplaced: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_UNPLACED} />,
};

/** No environment with this id, or no permission to see it. */
export const NotFound: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} id="no-such-environment" environment={null} />,
};

export const ErrorState: Story = {
  render: () => (
    <EnvironmentDetail
      {...ENVIRONMENT}
      environment={null}
      error={{ message: "Network error: failed to fetch" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_LONG} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <EnvironmentDetail {...ENVIRONMENT} environment={ENV_LONG} />
    </div>
  ),
};
