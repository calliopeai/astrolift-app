import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  ENV_LONG,
  ENV_STAGING_PAUSED,
  ENV_UNPLACED,
  ENVIRONMENT,
} from "@/components/screens/domains/domains-environments.fixtures";

import { EnvironmentDetail } from "./EnvironmentDetail";

const meta: Meta = {
  title: "Screens/Environments/EnvironmentDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <EnvironmentDetail {...ENVIRONMENT} /> };

export const Loading: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} loading environment={null} />,
};

/** Paused deploys and ingress, and no per-environment settings. */
export const EmptySettings: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_STAGING_PAUSED} />,
};

/** Not placed on a cluster: URL, cluster, provider and zone read as a dash. */
export const Unplaced: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_UNPLACED} />,
};

/**
 * The detail has no error state of its own: an unknown id, a failed
 * query, and a deep link past the list all resolve to not found.
 */
export const NotFound: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} id="no-such-environment" environment={null} />,
};

export const LongStrings: Story = {
  render: () => <EnvironmentDetail {...ENVIRONMENT} environment={ENV_LONG} />,
};
