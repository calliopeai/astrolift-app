import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { AgentRunsPanelView } from "./AgentRunsPanel";

import {
  AGENT_RUNS,
  AGENT_RUNS_LONG,
  FAILED,
  LOADING,
  PANEL,
  READY,
} from "./builder-operator.fixtures";

/** Agent runs on the Builder layout: the five newest runs of every status. */
const meta: Meta = { title: "Home/Panels/AgentRunsPanel", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const Grid = ({ children, width }: { children: React.ReactNode; width?: number }) => (
  <div style={width ? { width } : undefined}>
    <PanelGrid>{children}</PanelGrid>
  </div>
);

export const Full: Story = {
  render: () => (
    <Grid>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={AGENT_RUNS} count={212} {...READY} />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={[]} count={null} {...LOADING} />
    </Grid>
  ),
};

export const Empty: Story = {
  render: () => (
    <Grid>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={[]} count={0} {...READY} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={[]} count={null} {...FAILED} />
    </Grid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Grid>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={AGENT_RUNS_LONG} count={1} {...READY} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <AgentRunsPanelView panel={PANEL["agent-runs"]} rows={AGENT_RUNS} count={212} {...READY} />
    </Grid>
  ),
};
