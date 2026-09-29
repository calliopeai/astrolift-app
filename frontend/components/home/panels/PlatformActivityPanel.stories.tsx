import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { PlatformActivityPanelView } from "./PlatformActivityPanel";

import {
  FAILED,
  LOADING,
  PANEL,
  PLATFORM_RUNS,
  PLATFORM_RUNS_LONG,
  READY,
} from "./builder-operator.fixtures";

/** Platform activity on the Operator layout: the platform's workflow runs as a Feed. */
const meta: Meta = { title: "Home/Panels/PlatformActivityPanel", parameters: { layout: "padded" } };
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
      <PlatformActivityPanelView
        panel={PANEL["platform-activity"]}
        items={PLATFORM_RUNS}
        hasMore
        {...READY}
      />
    </Grid>
  ),
};

export const LoadingOlder: Story = {
  render: () => (
    <Grid>
      <PlatformActivityPanelView
        panel={PANEL["platform-activity"]}
        items={PLATFORM_RUNS}
        hasMore
        loadingMore
        {...READY}
      />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <PlatformActivityPanelView panel={PANEL["platform-activity"]} items={[]} {...LOADING} />
    </Grid>
  ),
};

export const Empty: Story = {
  render: () => (
    <Grid>
      <PlatformActivityPanelView panel={PANEL["platform-activity"]} items={[]} {...READY} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <PlatformActivityPanelView panel={PANEL["platform-activity"]} items={[]} {...FAILED} />
    </Grid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Grid>
      <PlatformActivityPanelView
        panel={PANEL["platform-activity"]}
        items={PLATFORM_RUNS_LONG}
        {...READY}
      />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <PlatformActivityPanelView
        panel={PANEL["platform-activity"]}
        items={PLATFORM_RUNS}
        hasMore
        {...READY}
      />
    </Grid>
  ),
};
