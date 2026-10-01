import { expect, fn, userEvent, within } from "storybook/test";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ClusterDetail } from "./ClusterDetail";
import { ClusterLiveStats } from "./ClusterLiveStats";
import { CLUSTERS, LIVE_STATS, LONG_CLUSTER, detailProps } from "./fixtures";

const meta: Meta = {
  title: "Screens/Clusters/List/ClusterDetail",
  parameters: {
    layout: "padded",
    nextjs: { navigation: { pathname: "/clusters/prd-us-west-2" } },
  },
};
export default meta;

type Story = StoryObj;

const liveStats = () => <ClusterLiveStats {...LIVE_STATS} />;

export const Managed: Story = {
  render: () => <ClusterDetail {...detailProps({ renderLiveStats: liveStats })} />,
};

export const Loading: Story = {
  render: () => <ClusterDetail {...detailProps({ cluster: undefined, loading: true })} />,
};

export const NotFound: Story = {
  render: () => <ClusterDetail {...detailProps({ cluster: undefined, slug: "missing" })} />,
};

/** Registered, never probed: no capabilities card, an action-required nudge. */
export const Registered: Story = {
  render: () => (
    <ClusterDetail
      {...detailProps({ slug: CLUSTERS[2].slug, cluster: CLUSTERS[2], renderLiveStats: liveStats })}
    />
  ),
};

export const Managing: Story = {
  render: () => (
    <ClusterDetail {...detailProps({ slug: CLUSTERS[1].slug, cluster: CLUSTERS[1] })} />
  ),
};

export const ManagementError: Story = {
  render: () => (
    <ClusterDetail {...detailProps({ slug: CLUSTERS[3].slug, cluster: CLUSTERS[3] })} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ClusterDetail
      {...detailProps({
        slug: LONG_CLUSTER.slug,
        cluster: LONG_CLUSTER,
        renderLiveStats: liveStats,
      })}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ClusterDetail
        {...detailProps({
          slug: LONG_CLUSTER.slug,
          cluster: LONG_CLUSTER,
          renderLiveStats: liveStats,
        })}
      />
    </div>
  ),
};

const refusedUnregister = fn(async () => {
  throw new Error("RAW_SERVER_REFUSAL_LITERAL");
});
export const UnregisterRefused: Story = {
  render: () => (
    <ClusterDetail {...detailProps({ cluster: CLUSTERS[2], onUnregister: refusedUnregister })} />
  ),
  play: async ({ canvasElement }) => {
    refusedUnregister.mockClear();
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Cluster actions" }));
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(body.getByRole("menuitem", { name: "Unregister" }));
    const dialog = within(await body.findByRole("alertdialog"));
    await userEvent.click(dialog.getByRole("button", { name: "Unregister" }));
    await expect(refusedUnregister).toHaveBeenCalledTimes(1);
    await expect(body.getByRole("alertdialog")).toBeVisible();
  },
};
