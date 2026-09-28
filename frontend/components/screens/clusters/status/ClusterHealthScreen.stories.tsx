import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DetailTabRow } from "@/components/DetailPageTabs";

import { ClusterHealthBody, type ClusterHealthBodyProps } from "./ClusterHealthScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import { CLUSTER, HEALTH, HEALTH_LONG, LONG_CLUSTER, WORKLOADS, WORKLOADS_LONG } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterHealth",
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
      active: label === "Health",
    }))}
  />
);

function Screen({
  cluster = CLUSTER,
  ...body
}: ClusterHealthBodyProps & { cluster?: typeof CLUSTER }) {
  return (
    <ClusterTabFrame
      slug={cluster.slug}
      cluster={cluster}
      loading={false}
      loadingTitle="Cluster health"
      tabLabel="Health"
      tabs={tabs}
    >
      <ClusterHealthBody {...body} />
    </ClusterTabFrame>
  );
}

export const Full: Story = { render: () => <Screen health={HEALTH} workloads={WORKLOADS} /> };

export const Loading: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: true }}
      workloads={{ rows: [], loading: true }}
    />
  ),
};

/** Reachable, nothing to report: no pods yet and no warnings. */
export const Empty: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: false }}
      workloads={{ rows: [], loading: false }}
    />
  ),
};

/** Pods reporting but failing, and warning events piling up. */
export const ErrorState: Story = {
  render: () => (
    <Screen
      health={{
        ...HEALTH,
        pods: [{ namespace: "astrolift-apps", phase: "Failed", count: 9 }],
      }}
      workloads={{
        loading: false,
        rows: WORKLOADS.rows.map((r) => ({ ...r, readyReplicas: 0, restartCount24h: 12 })),
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Screen cluster={LONG_CLUSTER} health={HEALTH_LONG} workloads={WORKLOADS_LONG} />,
};
