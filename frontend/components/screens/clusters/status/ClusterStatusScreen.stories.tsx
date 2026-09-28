import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DetailTabRow } from "@/components/DetailPageTabs";

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
  WORKFLOWS,
  WORKFLOWS_LONG,
  WORKLOADS,
  WORKLOADS_LONG,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterStatus",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const tabs = (
  <DetailTabRow
    ariaLabel="Cluster tabs"
    tabs={["Overview", "Status", "Health", "Activity", "Settings"].map((label) => ({
      key: label.toLowerCase(),
      label,
      href: "#",
      active: label === "Status",
    }))}
  />
);

const FULL: ClusterStatusBodyProps = {
  slug: CLUSTER.slug,
  liveState: LIVE_CONNECTED,
  metrics: <StatusMetricsCard slug={CLUSTER.slug} {...METRICS} />,
  workloads: <StatusWorkloadHealthCard {...WORKLOADS} />,
  liveHealth: <StatusLiveHealthCard {...HEALTH} />,
  workflows: <StatusRecentWorkflowsCard {...WORKFLOWS} />,
  lifecycle: <StatusLifecycleCard {...AUDIT} />,
};

function Screen(props: Partial<ClusterStatusBodyProps> & { cluster?: typeof CLUSTER }) {
  const { cluster = CLUSTER, ...body } = props;
  return (
    <ClusterTabFrame
      slug={cluster.slug}
      cluster={cluster}
      loading={false}
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={tabs}
    >
      <ClusterStatusBody {...FULL} slug={cluster.slug} {...body} />
    </ClusterTabFrame>
  );
}

/** Connected cluster, every card populated. */
export const Full: Story = { render: () => <Screen /> };

/** Heartbeat still loading: offline stand-ins until the live state lands. */
export const Loading: Story = {
  render: () => (
    <Screen liveState={LIVE_LOADING} lifecycle={<StatusLifecycleCard entries={[]} loading />} />
  ),
};

/** Connected, but every card's query is still in flight. */
export const CardsLoading: Story = {
  render: () => (
    <Screen
      metrics={<StatusMetricsCard slug={CLUSTER.slug} {...METRICS_LOADING} />}
      workloads={<StatusWorkloadHealthCard rows={[]} loading />}
      liveHealth={<StatusLiveHealthCard pods={[]} events={[]} loading />}
      workflows={<StatusRecentWorkflowsCard runs={[]} loading />}
      lifecycle={<StatusLifecycleCard entries={[]} loading />}
    />
  ),
};

/** Connected, nothing reported yet, no Prometheus endpoint. */
export const Empty: Story = {
  render: () => (
    <Screen
      metrics={<StatusMetricsCard slug={CLUSTER.slug} {...METRICS_NO_ENDPOINT} />}
      workloads={<StatusWorkloadHealthCard rows={[]} loading={false} />}
      liveHealth={<StatusLiveHealthCard pods={[]} events={[]} loading={false} />}
      workflows={<StatusRecentWorkflowsCard runs={[]} loading={false} />}
      lifecycle={<StatusLifecycleCard entries={[]} loading={false} />}
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
      workloads={<StatusWorkloadHealthCard {...WORKLOADS_LONG} />}
      liveHealth={<StatusLiveHealthCard {...HEALTH_LONG} />}
      workflows={<StatusRecentWorkflowsCard {...WORKFLOWS_LONG} />}
      lifecycle={<StatusLifecycleCard {...AUDIT_LONG} />}
    />
  ),
};
