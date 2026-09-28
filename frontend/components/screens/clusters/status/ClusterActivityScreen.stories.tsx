import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DetailTabRow } from "@/components/DetailPageTabs";

import { ClusterActivityBody, type ClusterActivityBodyProps } from "./ClusterActivityScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import { AUDIT, AUDIT_LONG, CLUSTER, LONG_CLUSTER, WORKFLOWS, WORKFLOWS_LONG } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/Status/ClusterActivity",
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
      active: label === "Activity",
    }))}
  />
);

function Screen({
  cluster = CLUSTER,
  ...body
}: ClusterActivityBodyProps & { cluster?: typeof CLUSTER }) {
  return (
    <ClusterTabFrame
      slug={cluster.slug}
      cluster={cluster}
      loading={false}
      loadingTitle="Cluster activity"
      tabLabel="Activity"
      tabs={tabs}
    >
      <ClusterActivityBody {...body} />
    </ClusterTabFrame>
  );
}

export const Full: Story = { render: () => <Screen workflows={WORKFLOWS} lifecycle={AUDIT} /> };

export const Loading: Story = {
  render: () => (
    <Screen workflows={{ runs: [], loading: true }} lifecycle={{ entries: [], loading: true }} />
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen workflows={{ runs: [], loading: false }} lifecycle={{ entries: [], loading: false }} />
  ),
};

/** Failed runs and failed mutations, with their first error shown. */
export const ErrorState: Story = {
  render: () => (
    <Screen
      workflows={{
        loading: false,
        runs: WORKFLOWS.runs.map((r) => ({ ...r, status: "FAILED" })),
      }}
      lifecycle={{
        loading: false,
        entries: AUDIT.entries.map((e) => ({
          ...e,
          success: false,
          errors: e.errors.length ? e.errors : ["Permission denied: cluster.manage"],
        })),
      }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Screen cluster={LONG_CLUSTER} workflows={WORKFLOWS_LONG} lifecycle={AUDIT_LONG} />,
};
