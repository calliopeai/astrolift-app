import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterTabs } from "./ClusterTabs";

const meta: Meta = {
  title: "Screens/Clusters/List/ClusterTabs",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Overview: Story = {
  render: () => <ClusterTabs slug="prd-us-west-2" active="overview" />,
};

/** No explicit tab: the active one is read from the pathname. */
export const FromPathname: Story = {
  parameters: { nextjs: { navigation: { pathname: "/clusters/prd-us-west-2/health" } } },
  render: () => <ClusterTabs slug="prd-us-west-2" />,
};

export const Settings: Story = {
  render: () => <ClusterTabs slug="prd-us-west-2" active="settings" />,
};

export const LongSlug: Story = {
  render: () => (
    <ClusterTabs
      slug="prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-slug"
      active="activity"
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ClusterTabs slug="prd-us-west-2" active="health" />
    </div>
  ),
};
