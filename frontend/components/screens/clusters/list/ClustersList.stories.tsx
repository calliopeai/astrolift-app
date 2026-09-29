import type { Decorator, Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { ClustersList, type ClustersListProps } from "./ClustersList";
import { CLUSTERS_LIST } from "./clusters-list";
import { CLUSTERS, FLEET, LONG_CLUSTER, listProps, serveClusters } from "./fixtures";
import type { ClusterRow } from "./use-clusters-list";

// List or cards is a per-person preference in localStorage; pin it per story
// so one story's mode never leaks into the next.
const withMode: Decorator = (Story, { parameters }) => {
  try {
    localStorage.setItem(
      `astrolift.list.${CLUSTERS_LIST.id}.mode`,
      JSON.stringify(parameters.mode ?? "list")
    );
  } catch {
    // storage unavailable: the list view
  }
  return <Story />;
};

const meta: Meta = {
  title: "Screens/Clusters/List/ClustersList",
  parameters: { layout: "padded" },
  decorators: [withMode],
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<ClustersListProps, "list">> & {
  fleet?: ClusterRow[];
  initial?: Partial<ListState>;
};

/** The screen over a fixture fleet, filtered and paged the way the server does it. */
function Clusters({ fleet = CLUSTERS, initial, ...patch }: Props) {
  const list = useLocalListState(CLUSTERS_LIST, initial);
  const { rows, totalCount } = serveClusters(fleet, {
    filters: list.filters,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return <ClustersList {...listProps({ rows, totalCount, ...patch })} list={list} />;
}

export const Full: Story = { render: () => <Clusters /> };

export const Loading: Story = { render: () => <Clusters fleet={[]} loading /> };

export const Empty: Story = { render: () => <Clusters fleet={[]} /> };

/** A search or chip that matches nothing: "No clusters match" and Clear. */
export const EmptyFiltered: Story = {
  render: () => <Clusters initial={{ filters: { provider: "aks" } }} />,
};

/** The Offline view with nothing offline. */
export const EmptyView: Story = {
  render: () => <Clusters fleet={CLUSTERS.slice(0, 2)} initial={{ view: "offline" }} />,
};

export const ErrorState: Story = {
  render: () => <Clusters fleet={[]} error={{ message: "upstream timed out" }} />,
};

/** Rows answer the previous search while the next loads. */
export const Refetching: Story = { render: () => <Clusters stale /> };

/** Mine: clusters the viewer registered. */
export const Mine: Story = { render: () => <Clusters initial={{ view: "mine" }} /> };

export const Offline: Story = { render: () => <Clusters initial={{ view: "offline" }} /> };

/** Provider and status chips, sorted by status then name. */
export const Filtered: Story = {
  render: () => (
    <Clusters
      fleet={FLEET}
      initial={{
        filters: { provider: "eks", status: "managed" },
        sort: [
          { key: "status", dir: "asc" },
          { key: "name", dir: "asc" },
        ],
      }}
    />
  ),
};

/** Sixty clusters: numbered pages, page 2 of 3. */
export const Paged: Story = {
  render: () => <Clusters fleet={FLEET} initial={{ page: 2 }} />,
};

export const Cards: Story = { parameters: { mode: "card" }, render: () => <Clusters /> };

export const CardsLoading: Story = {
  parameters: { mode: "card" },
  render: () => <Clusters fleet={[]} loading />,
};

export const CardsEmpty: Story = {
  parameters: { mode: "card" },
  render: () => <Clusters fleet={[]} />,
};

export const CardsError: Story = {
  parameters: { mode: "card" },
  render: () => <Clusters fleet={[]} error={{ message: "upstream timed out" }} />,
};

/** A 64-char SHA for a name, a 200-char ARN and an unbroken URL in the failure tooltip. */
export const LongStrings: Story = {
  render: () => <Clusters fleet={[LONG_CLUSTER, ...CLUSTERS]} />,
};

export const LongStringsCards: Story = {
  parameters: { mode: "card" },
  render: () => <Clusters fleet={[LONG_CLUSTER, ...CLUSTERS]} />,
};

/** The narrowest supported width: the table scrolls in its own frame, the page does not. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Clusters fleet={[LONG_CLUSTER, ...FLEET]} />
    </div>
  ),
};

/** Views are the header's tabs, register is a link to its page, and Admin ▾ switches functions. */
export const HeaderNavigation: Story = {
  render: () => <Clusters />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Offline" })).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "Mine" })).toBeInTheDocument();
    await expect(canvas.getByRole("link", { name: "Register cluster" })).toHaveAttribute(
      "href",
      "/clusters/new"
    );
    await userEvent.click(canvas.getByRole("button", { name: "Admin: switch" }));
    await expect(
      await within(document.body).findByRole("menuitem", { name: /Clusters/ })
    ).toBeInTheDocument();
  },
};
