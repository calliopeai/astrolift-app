import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { AgentRegistryPanel } from "./AgentRegistryPanel";
import { LONG_AGENTS, registryProps } from "./agents-list.fixtures";

const meta: Meta = { title: "Screens/Agents/List/AgentRegistryPanel" };
export default meta;

type Story = StoryObj;

export const Fleet: Story = {
  render: () => <AgentRegistryPanel {...registryProps()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("2 running")).toBeInTheDocument();
    await expect(canvas.getByText("Paused")).toBeInTheDocument();
    await expect(canvas.getByText("Service")).toBeInTheDocument();
  },
};

export const ProjectScoped: Story = {
  render: () => (
    <AgentRegistryPanel {...registryProps({ fleet: false, projectSlug: "platform" })} />
  ),
};

export const Loading: Story = {
  render: () => <AgentRegistryPanel {...registryProps({ agents: [], loading: true })} />,
};

export const EmptyFleet: Story = {
  render: () => <AgentRegistryPanel {...registryProps({ agents: [] })} />,
};

export const EmptyProject: Story = {
  render: () => (
    <AgentRegistryPanel {...registryProps({ agents: [], fleet: false, projectSlug: "support" })} />
  ),
};

/** Without the Agents module's canCreate, the empty state offers no register action. */
export const EmptyCannotCreate: Story = {
  render: () => <AgentRegistryPanel {...registryProps({ agents: [], canCreateAgent: false })} />,
};

/** The Registry tab has no error state: a failed query renders the empty card. */
export const QueryFailedShowsEmpty: Story = {
  render: () => <AgentRegistryPanel {...registryProps({ agents: [], loading: false })} />,
};

export const LongStrings: Story = {
  render: () => (
    <AgentRegistryPanel {...registryProps({ agents: LONG_AGENTS, liveByWorkloadId: new Map() })} />
  ),
};
