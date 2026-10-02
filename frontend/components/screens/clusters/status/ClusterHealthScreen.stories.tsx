import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { selectRows } from "@/components/list/select-rows";
import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  useClusterWorkloadsListDefinition,
  CLUSTER_WORKLOADS_SELECT,
} from "./cluster-workloads-list";
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
import type { WorkloadRow } from "./types";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterHealth",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

interface WorkloadsFixture {
  rows: WorkloadRow[];
  loading: boolean;
  error: string | null;
}

/** The body over fixture workloads, filtered, sorted and paged the way the hook does it. */
function Screen({
  cluster = CLUSTER,
  health,
  workloads,
  initial,
}: {
  cluster?: typeof CLUSTER;
  health: ClusterHealthBodyProps["health"];
  workloads: WorkloadsFixture;
  initial?: Partial<ListState>;
}) {
  const definition = useClusterWorkloadsListDefinition();
  const list = useLocalListState(definition, initial);
  const { rows, totalCount } = selectRows(
    workloads.rows,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    CLUSTER_WORKLOADS_SELECT
  );
  return (
    <ClusterTabFrame slug={cluster.slug} cluster={cluster} loading={false} active="health">
      <ClusterHealthBody
        health={health}
        workloads={{
          list,
          rows,
          totalCount,
          loading: workloads.loading,
          error: workloads.error ? { message: workloads.error } : null,
          onRetry: () => {},
        }}
      />
    </ClusterTabFrame>
  );
}

export const Full: Story = { render: () => <Screen health={HEALTH} workloads={WORKLOADS} /> };

/** More warnings behind the window: Load older at the end of the frame. */
export const MoreWarnings: Story = {
  render: () => (
    <Screen
      health={{
        ...HEALTH,
        moreEvents: { hasMore: true, loadingMore: false, onLoadMore: () => {} },
      }}
      workloads={WORKLOADS}
    />
  ),
};

/** The Unhealthy view: the workloads short of desired replicas. */
export const Unhealthy: Story = {
  render: () => <Screen health={HEALTH} workloads={WORKLOADS} initial={{ view: "unhealthy" }} />,
};

export const Loading: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: true, ...QUERY_OK }}
      workloads={{ rows: [], loading: true, error: null }}
    />
  ),
};

/** Empty observations do not establish reachability or a healthy cluster. */
export const Empty: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: false, ...QUERY_OK }}
      workloads={{ rows: [], loading: false, error: null }}
    />
  ),
};

/** Both queries failed: each panel keeps its frame and offers a retry. */
export const LoadError: Story = {
  render: () => (
    <Screen
      health={{ pods: [], events: [], loading: false, ...QUERY_FAILED }}
      workloads={{ rows: [], loading: false, error: QUERY_FAILED.error }}
    />
  ),
};

/** Keep reports visible while qualifying a failed independent refresh. */
export const CachedError: Story = {
  render: () => (
    <Screen
      health={{ ...HEALTH, error: QUERY_FAILED.error }}
      workloads={{ ...WORKLOADS, error: QUERY_FAILED.error }}
    />
  ),
};

export const Refreshing: Story = {
  render: () => (
    <Screen health={{ ...HEALTH, loading: true }} workloads={{ ...WORKLOADS, loading: true }} />
  ),
};

/** Unknown provider tokens, malformed observations and zero-sized deployments. */
export const UnknownObservations: Story = {
  render: () => (
    <Screen
      health={{
        ...HEALTH,
        pods: [{ namespace: "raw-namespace", phase: "FuturePhase", count: -1 }],
        events: [{ ...HEALTH.events[0], lastSeen: "invalid-time", count: -1 }],
      }}
      workloads={{
        ...WORKLOADS,
        rows: [
          {
            ...WORKLOADS.rows[0],
            readyReplicas: -1,
            restartCount24h: -1,
            lastImageDeployedAt: "invalid-time",
          },
          { ...WORKLOADS.rows[1], readyReplicas: 0, desiredReplicas: 0 },
        ],
      }}
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
