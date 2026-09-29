import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DispatchTabView } from "../list/DispatchTab";
import { AGENT, DISPATCH, LONG_AGENT } from "../list/agents-dispatch-secrets.fixtures";
import { RunAgentScreen } from "./RunAgentScreen";

const meta: Meta = {
  title: "Screens/Agents/Runs/RunAgentScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Opened from an agent's Run with inputs: that agent is picked. */
export const Full: Story = {
  render: () => (
    <RunAgentScreen>
      <DispatchTabView {...DISPATCH} defaultAgentSlug={AGENT.slug} />
    </RunAgentScreen>
  ),
};

/** Opened from Runs: no agent picked yet. */
export const NoAgentPicked: Story = {
  render: () => (
    <RunAgentScreen>
      <DispatchTabView {...DISPATCH} />
    </RunAgentScreen>
  ),
};

export const Loading: Story = {
  render: () => (
    <RunAgentScreen>
      <DispatchTabView {...DISPATCH} agents={[]} fleetLoading />
    </RunAgentScreen>
  ),
};

export const Empty: Story = {
  render: () => (
    <RunAgentScreen>
      <DispatchTabView {...DISPATCH} agents={[]} />
    </RunAgentScreen>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <RunAgentScreen>
      <DispatchTabView {...DISPATCH} agents={[LONG_AGENT]} defaultAgentSlug={LONG_AGENT.slug} />
    </RunAgentScreen>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <RunAgentScreen>
        <DispatchTabView {...DISPATCH} agents={[LONG_AGENT]} defaultAgentSlug={LONG_AGENT.slug} />
      </RunAgentScreen>
    </div>
  ),
};
