import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleClusters } from "./ZentinelleClusters";
import { CONNECTED, DEGRADED_CONNECTION, HEALTHY_CONNECTION } from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Clusters",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const actions = { ...CONNECTED.clusters, canManage: true, canRotate: true };

export const Healthy: Story = {
  render: () => <ZentinelleClusters {...actions} connection={HEALTHY_CONNECTION} />,
};

export const Degraded: Story = {
  render: () => <ZentinelleClusters {...actions} connection={DEGRADED_CONNECTION} />,
};

export const Busy: Story = {
  render: () => (
    <ZentinelleClusters
      {...actions}
      busyClusterId={HEALTHY_CONNECTION.clusters[0].clusterId}
      connection={HEALTHY_CONNECTION}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <ZentinelleClusters {...actions} connection={{ ...HEALTHY_CONNECTION, clusters: [] }} />
  ),
};

export const ChoicesLoading: Story = {
  render: () => (
    <ZentinelleClusters
      {...actions}
      choices={{ items: [], loading: true, search: "" }}
      connection={HEALTHY_CONNECTION}
    />
  ),
};

/** Status only: no register, toggle, rotate or unregister. */
export const ReadOnly: Story = {
  render: () => (
    <ZentinelleClusters
      {...actions}
      canManage={false}
      canRotate={false}
      connection={DEGRADED_CONNECTION}
    />
  ),
};

export const RefusedWrite: Story = {
  render: () => (
    <ZentinelleClusters
      {...actions}
      onRotate={async () => "Zentinelle could not be reached"}
      connection={HEALTHY_CONNECTION}
    />
  ),
};
