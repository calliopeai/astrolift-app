import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleUsage } from "./ZentinelleUsage";
import { CONNECTED, UPSTREAM_ERROR } from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Usage",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const { filter, usage } = CONNECTED.usage;

export const Full: Story = { render: () => <ZentinelleUsage filter={filter} usage={usage} /> };

export const Loading: Story = {
  render: () => (
    <ZentinelleUsage filter={filter} usage={{ data: [], loading: true, error: null }} />
  ),
};

export const Empty: Story = {
  render: () => (
    <ZentinelleUsage filter={filter} usage={{ data: [], loading: false, error: null }} />
  ),
};

export const UpstreamError: Story = {
  render: () => (
    <ZentinelleUsage filter={filter} usage={{ data: [], loading: false, error: UPSTREAM_ERROR }} />
  ),
};
