import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { AlertsPanelView } from "./AlertsPanel";

import { ALERTS, ALERTS_LONG, FAILED, LOADING, PANEL, READY } from "./builder-operator.fixtures";

/** Alerts on the Operator layout: firing first, the worst severity first. */
const meta: Meta = { title: "Home/Panels/AlertsPanel", parameters: { layout: "padded" } };
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
      <AlertsPanelView panel={PANEL.alerts} rows={ALERTS} count={2} {...READY} />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <AlertsPanelView panel={PANEL.alerts} rows={[]} count={null} {...LOADING} />
    </Grid>
  ),
};

export const NoneFiring: Story = {
  render: () => (
    <Grid>
      <AlertsPanelView panel={PANEL.alerts} rows={[]} count={0} {...READY} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <AlertsPanelView panel={PANEL.alerts} rows={[]} count={null} {...FAILED} />
    </Grid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Grid>
      <AlertsPanelView panel={PANEL.alerts} rows={ALERTS_LONG} count={1} {...READY} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <AlertsPanelView panel={PANEL.alerts} rows={ALERTS} count={2} {...READY} />
    </Grid>
  ),
};
