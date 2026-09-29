import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentDiscoveryStepView } from "./AgentDiscoveryStep";
import {
  DISCOVERY_EMPTY,
  DISCOVERY_ERROR,
  DISCOVERY_FOUND,
  DISCOVERY_LONG,
  DISCOVERY_SCANNING,
} from "./agents-wizard-a.fixtures";

const meta: Meta = {
  title: "Screens/Agents/New/AgentDiscoveryStep",
};
export default meta;

type Story = StoryObj;

/** First scan in flight: skeleton rows. */
export const Loading: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_SCANNING} />,
};

/** Scan finished, no agent manifests in the repo. */
export const Empty: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_EMPTY} />,
};

export const ScanFailed: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_ERROR} />,
};

/** Two new agents and one already registered. */
export const Full: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_FOUND} />,
};

/** Re-scan in flight with results already on screen. */
export const Rescanning: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_FOUND} fetchState="scanning" />,
};

export const LongStrings: Story = {
  render: () => <AgentDiscoveryStepView {...DISCOVERY_LONG} />,
};
