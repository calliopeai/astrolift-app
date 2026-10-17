import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleOverview } from "./ZentinelleOverview";
import {
  CONNECTED,
  DEGRADED_CONNECTION,
  HEALTHY_CONNECTION,
  UPSTREAM_ERROR,
} from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Overview",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const usage = CONNECTED.health.usage;

export const Healthy: Story = {
  render: () => <ZentinelleOverview connection={HEALTHY_CONNECTION} usage={usage} />,
};

export const Degraded: Story = {
  render: () => <ZentinelleOverview connection={DEGRADED_CONNECTION} usage={usage} />,
};

export const GatewaysOffForInstall: Story = {
  render: () => (
    <ZentinelleOverview
      connection={{ ...HEALTHY_CONNECTION, gatewayFeatureEnabled: false }}
      usage={usage}
    />
  ),
};

export const NoClusters: Story = {
  render: () => (
    <ZentinelleOverview connection={{ ...HEALTHY_CONNECTION, clusters: [] }} usage={usage} />
  ),
};

export const Loading: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={{ data: null, loading: true, error: null }}
    />
  ),
};

export const UsageError: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={{ data: null, loading: false, error: UPSTREAM_ERROR }}
    />
  ),
};

/** Usage only: the viewer cannot read the connection. */
export const UsageOnly: Story = {
  render: () => <ZentinelleOverview connection={null} usage={usage} />,
};
