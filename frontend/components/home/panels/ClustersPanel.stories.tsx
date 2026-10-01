import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { PanelGrid } from "@/components/panel/Panel";

import { clustersWorstFirst } from "./builder-operator-model";
import { ClustersPanelView } from "./ClustersPanel";

import {
  CLUSTERS,
  CLUSTERS_HEALTHY,
  CLUSTERS_LONG,
  FAILED,
  LOADING,
  PANEL,
  READY,
} from "./builder-operator.fixtures";

/** Clusters on the Operator layout: counts by health, the least healthy first. */
const meta: Meta = { title: "Home/Panels/ClustersPanel", parameters: { layout: "padded" } };
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
      <ClustersPanelView
        panel={PANEL.clusters}
        rows={clustersWorstFirst(CLUSTERS)}
        count={CLUSTERS.length}
        {...READY}
      />
    </Grid>
  ),
};

export const AllHealthy: Story = {
  render: () => (
    <Grid>
      <ClustersPanelView panel={PANEL.clusters} rows={CLUSTERS_HEALTHY} count={1} {...READY} />
    </Grid>
  ),
};

export const UnknownHeartbeat: Story = {
  render: () => (
    <Grid width={768}>
      <ClustersPanelView
        panel={PANEL.clusters}
        rows={[
          {
            ...CLUSTERS_HEALTHY[0],
            heartbeatStatus: "LITERAL_FUTURE_CONNECTION_STATUS_WITH_LONG_PROVIDER_DETAILS",
          },
        ]}
        count={1}
        {...READY}
      />
    </Grid>
  ),
};

export const Loading: Story = {
  render: () => (
    <Grid>
      <ClustersPanelView panel={PANEL.clusters} rows={[]} count={null} {...LOADING} />
    </Grid>
  ),
};

export const Empty: Story = {
  render: () => (
    <Grid>
      <ClustersPanelView panel={PANEL.clusters} rows={[]} count={0} {...READY} />
    </Grid>
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Grid>
      <ClustersPanelView panel={PANEL.clusters} rows={[]} count={null} {...FAILED} />
    </Grid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Grid>
      <ClustersPanelView panel={PANEL.clusters} rows={CLUSTERS_LONG} count={1} {...READY} />
    </Grid>
  ),
};

export const Width768: Story = {
  render: () => (
    <Grid width={768}>
      <ClustersPanelView
        panel={PANEL.clusters}
        rows={clustersWorstFirst(CLUSTERS)}
        count={CLUSTERS.length}
        {...READY}
      />
    </Grid>
  ),
};
