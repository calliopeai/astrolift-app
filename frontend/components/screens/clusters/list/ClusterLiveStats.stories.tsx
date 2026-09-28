import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterLiveStats } from "./ClusterLiveStats";
import { LIVE_STATS } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/ClusterLiveStats",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Live: Story = { render: () => <ClusterLiveStats {...LIVE_STATS} /> };

export const Loading: Story = {
  render: () => (
    <ClusterLiveStats appCount={undefined} appLoading metrics={undefined} metricsLoading />
  ),
};

/** No Prometheus on the cluster: the metric tiles degrade to a dash. */
export const PrometheusUnavailable: Story = {
  render: () => (
    <ClusterLiveStats
      {...LIVE_STATS}
      metrics={{
        available: false,
        nodeCount: null,
        podRunningRatio: null,
        cpuUtilization: null,
        memoryUtilization: null,
        deploymentReadyRatio: null,
      }}
    />
  ),
};

/** Query failed or has not answered: every tile shows a dash. */
export const NoData: Story = {
  render: () => (
    <ClusterLiveStats
      appCount={undefined}
      appLoading={false}
      metrics={undefined}
      metricsLoading={false}
    />
  ),
};

export const Unhealthy: Story = {
  render: () => (
    <ClusterLiveStats
      appCount={123456}
      appLoading={false}
      metrics={{
        available: true,
        nodeCount: 240,
        podRunningRatio: 0.41,
        cpuUtilization: 0.98,
        memoryUtilization: 0.995,
        deploymentReadyRatio: 0.5,
      }}
      metricsLoading={false}
    />
  ),
};
