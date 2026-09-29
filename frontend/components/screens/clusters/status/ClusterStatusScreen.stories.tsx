import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  ClusterStatusBody,
  type ClusterStatusBodyProps,
  StatusLifecycleCard,
  StatusLiveHealthCard,
  StatusMetricsCard,
  StatusRecentWorkflowsCard,
  StatusWorkloadHealthCard,
} from "./ClusterStatusScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import {
  AUDIT,
  AUDIT_LONG,
  CLUSTER,
  HEALTH,
  HEALTH_LONG,
  LIVE_CONNECTED,
  LIVE_LONG,
  LIVE_LOADING,
  LIVE_NEVER_SEEN,
  LIVE_OFFLINE,
  LONG_CLUSTER,
  METRICS,
  METRICS_LOADING,
  METRICS_LONG,
  METRICS_NO_ENDPOINT,
  METRICS_UNREACHABLE,
  QUERY_FAILED,
  QUERY_OK,
  WORKFLOWS,
  WORKFLOWS_LONG,
  WORKLOADS,
  WORKLOADS_LONG,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterStatus",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const FULL: ClusterStatusBodyProps = {
  slug: CLUSTER.slug,
  liveState: LIVE_CONNECTED,
  metrics: <StatusMetricsCard slug={CLUSTER.slug} {...METRICS} />,
  workloads: <StatusWorkloadHealthCard slug={CLUSTER.slug} {...WORKLOADS} />,
  liveHealth: <StatusLiveHealthCard {...HEALTH} />,
  workflows: <StatusRecentWorkflowsCard slug={CLUSTER.slug} {...WORKFLOWS} />,
  lifecycle: <StatusLifecycleCard slug={CLUSTER.slug} {...AUDIT} />,
};

function Screen(props: Partial<ClusterStatusBodyProps> & { cluster?: typeof CLUSTER }) {
  const { cluster = CLUSTER, ...body } = props;
  return (
    <ClusterTabFrame slug={cluster.slug} cluster={cluster} loading={false} active="status">
      <ClusterStatusBody {...FULL} slug={cluster.slug} {...body} />
    </ClusterTabFrame>
  );
}

/** Connected cluster, every card populated. */
export const Full: Story = { render: () => <Screen /> };

/** Heartbeat still loading: offline stand-ins until the live state lands. */
export const Loading: Story = {
  render: () => (
    <Screen
      liveState={LIVE_LOADING}
      lifecycle={<StatusLifecycleCard slug={CLUSTER.slug} entries={[]} loading {...QUERY_OK} />}
    />
  ),
};

/** Connected, but every card's query is still in flight. */
export const CardsLoading: Story = {
  render: () => (
    <Screen
      metrics={<StatusMetricsCard slug={CLUSTER.slug} {...METRICS_LOADING} />}
      workloads={<StatusWorkloadHealthCard slug={CLUSTER.slug} rows={[]} loading {...QUERY_OK} />}
      liveHealth={<StatusLiveHealthCard pods={[]} events={[]} loading {...QUERY_OK} />}
      workflows={<StatusRecentWorkflowsCard slug={CLUSTER.slug} runs={[]} loading {...QUERY_OK} />}
      lifecycle={<StatusLifecycleCard slug={CLUSTER.slug} entries={[]} loading {...QUERY_OK} />}
    />
  ),
};

/** Connected, nothing reported yet, no Prometheus endpoint. */
export const Empty: Story = {
  render: () => (
    <Screen
      metrics={<StatusMetricsCard slug={CLUSTER.slug} {...METRICS_NO_ENDPOINT} />}
      workloads={
        <StatusWorkloadHealthCard slug={CLUSTER.slug} rows={[]} loading={false} {...QUERY_OK} />
      }
      liveHealth={<StatusLiveHealthCard pods={[]} events={[]} loading={false} {...QUERY_OK} />}
      workflows={
        <StatusRecentWorkflowsCard slug={CLUSTER.slug} runs={[]} loading={false} {...QUERY_OK} />
      }
      lifecycle={
        <StatusLifecycleCard slug={CLUSTER.slug} entries={[]} loading={false} {...QUERY_OK} />
      }
    />
  ),
};

/** The agent stopped reporting: driver cards swap for offline stand-ins. */
export const Offline: Story = { render: () => <Screen liveState={LIVE_OFFLINE} /> };

/** No agent has ever reported. */
export const NoAgent: Story = { render: () => <Screen liveState={LIVE_NEVER_SEEN} /> };

/** Prometheus is configured but unreachable / cluster-internal. */
export const PrometheusError: Story = {
  render: () => (
    <Screen metrics={<StatusMetricsCard slug={CLUSTER.slug} {...METRICS_UNREACHABLE} />} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Screen
      cluster={LONG_CLUSTER}
      liveState={LIVE_LONG}
      metrics={<StatusMetricsCard slug={LONG_CLUSTER.slug} {...METRICS_LONG} />}
      workloads={<StatusWorkloadHealthCard slug={CLUSTER.slug} {...WORKLOADS_LONG} />}
      liveHealth={<StatusLiveHealthCard {...HEALTH_LONG} />}
      workflows={<StatusRecentWorkflowsCard slug={CLUSTER.slug} {...WORKFLOWS_LONG} />}
      lifecycle={<StatusLifecycleCard slug={CLUSTER.slug} {...AUDIT_LONG} />}
    />
  ),
};

/** Every query failed: each panel keeps its frame and offers a retry. */
export const LoadError: Story = {
  render: () => (
    <Screen
      liveState={{ state: null, loading: false, ...QUERY_FAILED }}
      lifecycle={
        <StatusLifecycleCard slug={CLUSTER.slug} entries={[]} loading={false} {...QUERY_FAILED} />
      }
    />
  ),
};

/** Connected, but the driver-backed queries failed. */
export const CardsError: Story = {
  render: () => (
    <Screen
      metrics={
        <StatusMetricsCard
          slug={CLUSTER.slug}
          {...METRICS}
          range={null}
          instant={null}
          {...QUERY_FAILED}
        />
      }
      workloads={
        <StatusWorkloadHealthCard slug={CLUSTER.slug} rows={[]} loading={false} {...QUERY_FAILED} />
      }
      liveHealth={<StatusLiveHealthCard pods={[]} events={[]} loading={false} {...QUERY_FAILED} />}
      workflows={
        <StatusRecentWorkflowsCard
          slug={CLUSTER.slug}
          runs={[]}
          loading={false}
          {...QUERY_FAILED}
        />
      }
      lifecycle={
        <StatusLifecycleCard slug={CLUSTER.slug} entries={[]} loading={false} {...QUERY_FAILED} />
      }
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen
        cluster={LONG_CLUSTER}
        liveState={LIVE_LONG}
        metrics={<StatusMetricsCard slug={LONG_CLUSTER.slug} {...METRICS_LONG} />}
        workloads={<StatusWorkloadHealthCard slug={CLUSTER.slug} {...WORKLOADS_LONG} />}
        liveHealth={<StatusLiveHealthCard {...HEALTH_LONG} />}
        workflows={<StatusRecentWorkflowsCard slug={CLUSTER.slug} {...WORKFLOWS_LONG} />}
        lifecycle={<StatusLifecycleCard slug={CLUSTER.slug} {...AUDIT_LONG} />}
      />
    </div>
  ),
};
