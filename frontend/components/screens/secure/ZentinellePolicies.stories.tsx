import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinellePolicies } from "./ZentinellePolicies";
import { CONNECTED, UPSTREAM_ERROR } from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Policies",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const { filter, policies } = CONNECTED.policies;

export const Full: Story = {
  render: () => <ZentinellePolicies filter={filter} policies={policies} />,
};

export const Loading: Story = {
  render: () => (
    <ZentinellePolicies filter={filter} policies={{ data: [], loading: true, error: null }} />
  ),
};

export const Empty: Story = {
  render: () => (
    <ZentinellePolicies filter={filter} policies={{ data: [], loading: false, error: null }} />
  ),
};

export const UpstreamError: Story = {
  render: () => (
    <ZentinellePolicies
      filter={filter}
      policies={{ data: [], loading: false, error: UPSTREAM_ERROR }}
    />
  ),
};
