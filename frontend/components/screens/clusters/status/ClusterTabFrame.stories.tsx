import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DetailTabRow } from "@/components/DetailPageTabs";

import { ClusterTabFrame } from "./ClusterTabFrame";
import { CLUSTER, LONG_CLUSTER } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterTabFrame",
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

const body = <p className="text-muted-foreground text-sm">Tab body</p>;

export const Found: Story = {
  render: () => (
    <ClusterTabFrame
      slug={CLUSTER.slug}
      cluster={CLUSTER}
      loading={false}
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={tabs}
    >
      {body}
    </ClusterTabFrame>
  ),
};

export const Loading: Story = {
  render: () => (
    <ClusterTabFrame
      slug="prod-east"
      cluster={null}
      loading
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={tabs}
    >
      {body}
    </ClusterTabFrame>
  ),
};

export const NotFound: Story = {
  render: () => (
    <ClusterTabFrame
      slug="no-such-cluster"
      cluster={null}
      loading={false}
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={tabs}
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
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={tabs}
    >
      {body}
    </ClusterTabFrame>
  ),
};
