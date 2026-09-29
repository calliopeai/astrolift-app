import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MoreHorizontalIcon, PlayIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import { ClusterHeader } from "./ClusterHeader";
import { CLUSTERS, LONG_CLUSTER } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/List/ClusterHeader",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const MENU = (
  <Button size="icon" variant="ghost" className="size-8" aria-label="Cluster actions">
    <MoreHorizontalIcon className="size-4" />
  </Button>
);

const ACTION = (
  <Button size="sm">
    <PlayIcon className="size-4" />
    Bring into management
  </Button>
);

export const Managed: Story = {
  render: () => (
    <ClusterHeader
      slug={CLUSTERS[0].slug}
      cluster={CLUSTERS[0]}
      active="overview"
      primaryAction={
        <Button size="sm" variant="outline">
          Refresh setup
        </Button>
      }
      menu={MENU}
    />
  ),
};

export const Registered: Story = {
  render: () => (
    <ClusterHeader
      slug={CLUSTERS[2].slug}
      cluster={CLUSTERS[2]}
      active="status"
      primaryAction={ACTION}
      menu={MENU}
    />
  ),
};

/** Inactive and failed: the dot, the label and "inactive". */
export const ErrorInactive: Story = {
  render: () => (
    <ClusterHeader slug={CLUSTERS[3].slug} cluster={CLUSTERS[3]} active="health" menu={MENU} />
  ),
};

export const Loading: Story = {
  render: () => <ClusterHeader slug="prd-us-west-2" cluster={null} loading active="overview" />,
};

export const NotFound: Story = {
  render: () => <ClusterHeader slug="missing" cluster={null} />,
};

/** No explicit tab: the pathname picks it. */
export const FromPathname: Story = {
  parameters: { nextjs: { navigation: { pathname: "/clusters/prd-us-west-2/activity" } } },
  render: () => <ClusterHeader slug="prd-us-west-2" cluster={CLUSTERS[0]} menu={MENU} />,
};

export const LongStrings: Story = {
  render: () => (
    <ClusterHeader
      slug={LONG_CLUSTER.slug}
      cluster={LONG_CLUSTER}
      active="settings"
      primaryAction={ACTION}
      menu={MENU}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ClusterHeader
        slug={LONG_CLUSTER.slug}
        cluster={LONG_CLUSTER}
        active="overview"
        primaryAction={ACTION}
        menu={MENU}
      />
    </div>
  ),
};
