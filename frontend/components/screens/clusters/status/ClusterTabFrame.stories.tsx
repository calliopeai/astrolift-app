import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterTabFrame } from "./ClusterTabFrame";
import { CLUSTER, LONG_CLUSTER, QUERY_FAILED } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterTabFrame",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const body = <p className="text-muted-foreground text-sm">Tab body</p>;

export const Found: Story = {
  render: () => (
    <ClusterTabFrame slug={CLUSTER.slug} cluster={CLUSTER} loading={false} active="status">
      {body}
    </ClusterTabFrame>
  ),
};

export const Loading: Story = {
  render: () => (
    <ClusterTabFrame slug="prod-east" cluster={null} loading active="status">
      {body}
    </ClusterTabFrame>
  ),
};

export const NotFound: Story = {
  render: () => (
    <ClusterTabFrame slug="no-such-cluster" cluster={null} loading={false} active="status">
      {body}
    </ClusterTabFrame>
  ),
};

/** The cluster list failed to load: the error and a retry, never a blank page. */
export const LoadError: Story = {
  render: () => (
    <ClusterTabFrame
      slug="prod-east"
      cluster={null}
      loading={false}
      error={QUERY_FAILED.error}
      onRetry={QUERY_FAILED.refetch}
      active="status"
    >
      {body}
    </ClusterTabFrame>
  ),
};

export const CachedReadFailed: Story = {
  render: () => (
    <ClusterTabFrame
      slug={CLUSTER.slug}
      cluster={CLUSTER}
      loading={false}
      error="RAW_CLUSTER_READ_DIAGNOSTIC"
      onRetry={QUERY_FAILED.refetch}
      active="status"
    >
      {body}
    </ClusterTabFrame>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ClusterTabFrame
      slug={LONG_CLUSTER.slug}
      cluster={LONG_CLUSTER}
      loading={false}
      active="health"
    >
      {body}
    </ClusterTabFrame>
  ),
};

/** At 768px the header wraps and the tab row scrolls inside itself. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ClusterTabFrame
        slug={LONG_CLUSTER.slug}
        cluster={LONG_CLUSTER}
        loading={false}
        active="activity"
      >
        {body}
      </ClusterTabFrame>
    </div>
  ),
};
