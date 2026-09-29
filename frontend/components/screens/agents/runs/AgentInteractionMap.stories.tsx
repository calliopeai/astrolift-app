import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentInteractionMapView } from "./AgentInteractionMap";
import { INTERACTION_MAP, LONG_INTERACTIONS } from "./agent-runs.fixtures";

const meta: Meta = { title: "Screens/Agents/Runs/AgentInteractionMap" };
export default meta;

type Story = StoryObj;

export const Live: Story = { render: () => <AgentInteractionMapView {...INTERACTION_MAP} /> };

export const Settled: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="completed" isTerminal />,
};

export const Loading: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} loading />,
};

export const Empty: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} />,
};

export const LoadError: Story = {
  render: () => (
    <AgentInteractionMapView
      {...INTERACTION_MAP}
      interactions={[]}
      error="Response not successful: Received status code 502"
    />
  ),
};

export const LongNames: Story = {
  render: () => <AgentInteractionMapView {...INTERACTION_MAP} interactions={LONG_INTERACTIONS} />,
};
