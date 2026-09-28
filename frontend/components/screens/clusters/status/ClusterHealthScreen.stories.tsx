import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterHealthBody, type ClusterHealthBodyProps } from "./ClusterHealthScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import {
  CLUSTER,
  HEALTH,
  HEALTH_LONG,
  LONG_CLUSTER,
  QUERY_FAILED,
  QUERY_OK,
  WORKLOADS,
  WORKLOADS_LONG,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterHealth",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Screen({
  cluster = CLUSTER,
  ...body
}: ClusterHealthBodyProps & { cluster?: typeof CLUSTER }) {
  return (
    <ClusterTabFrame slug={cluster.slug} cluster={cluster} loading={false} active="health">
      <ClusterHealthBody {...body} />
    </ClusterTabFrame>
  );
}

export const Full: Story = { render: () => <Screen health={HEALTH} workloads={WORKLOADS} /> };

export const Loading: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: true, ...QUERY_OK }}
      workloads={{ rows: [], loading: true, ...QUERY_OK }}
    />
  ),
};

/** Reachable, nothing to report: no pods yet and no warnings. */
export const Empty: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: false, ...QUERY_OK }}
      workloads={{ rows: [], loading: false, ...QUERY_OK }}
    />
  ),
};

/** Both queries failed: each panel keeps its frame and offers a retry. */
export const LoadError: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: false, ...QUERY_FAILED }}
      workloads={{ rows: [], loading: false, ...QUERY_FAILED }}
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
        ...WORKLOADS,
        rows: WORKLOADS.rows.map((r) => ({ ...r, readyReplicas: 0, restartCount24h: 12 })),
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Screen cluster={LONG_CLUSTER} health={HEALTH_LONG} workloads={WORKLOADS_LONG} />,
};

/** At 768px the workload table scrolls inside its panel; the page never widens. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen cluster={LONG_CLUSTER} health={HEALTH_LONG} workloads={WORKLOADS_LONG} />
    </div>
  ),
};
