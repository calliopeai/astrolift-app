import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { ActivityPanelView } from "./ActivityPanel";

import { ACTIVITY, ACTIVITY_LONG, FAILED, PANEL } from "./builder-operator.fixtures";

/** Activity on the Builder layout: the organization's lifecycle events as a Feed. */
const meta: Meta = { title: "Home/Panels/ActivityPanel", parameters: { layout: "padded" } };
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
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY} />
    </Grid>
  ),
};

export const LoadingOlder: Story = {
  render: () => (
    <Grid>
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY} loadingMore />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY} items={[]} loading hasMore={false} />
    </Grid>
  ),
};

export const Empty: Story = {
  render: () => (
    <Grid>
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY} items={[]} hasMore={false} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <ActivityPanelView
        panel={PANEL.activity}
        {...ACTIVITY}
        items={[]}
        hasMore={false}
        error={FAILED.error}
      />
    </Grid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Grid>
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY_LONG} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <ActivityPanelView panel={PANEL.activity} {...ACTIVITY} />
    </Grid>
  ),
};
