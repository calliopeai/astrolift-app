import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { selectRows } from "@/components/list/select-rows";
import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  DOMAIN_ACTIVE,
  DOMAIN_LONG,
  DOMAIN_PROVISIONING,
  DOMAIN_UNPROVISIONED,
  MANAGED_DOMAINS,
  type ManagedDomainsFixture,
} from "./domains-environments.fixtures";
import { MANAGED_DOMAINS_LIST, MANAGED_DOMAINS_SELECT } from "./managed-domains-list";
import { ManagedDomainsScreen } from "./ManagedDomainsScreen";

const meta: Meta = {
  title: "Screens/Domains/ManagedDomainsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The screen over fixture zones, filtered and paged the way the hook does it. */
function Screen({
  initial,
  domains,
  ...props
}: ManagedDomainsFixture & { initial?: Partial<ListState> }) {
  const list = useLocalListState(MANAGED_DOMAINS_LIST, initial);
  const { state } = list;
  const page = selectRows(
    domains,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    MANAGED_DOMAINS_SELECT
  );
  return (
    <ManagedDomainsScreen {...props} list={list} rows={page.rows} totalCount={page.totalCount} />
  );
}

/** One zone each: active with nameservers, provisioning, never provisioned. */
export const Full: Story = { render: () => <Screen {...MANAGED_DOMAINS} /> };

export const Loading: Story = {
  render: () => <Screen {...MANAGED_DOMAINS} loading domains={[]} />,
};

export const Empty: Story = { render: () => <Screen {...MANAGED_DOMAINS} domains={[]} /> };

export const EmptyFiltered: Story = {
  render: () => <Screen {...MANAGED_DOMAINS} initial={{ q: "no-such-zone" }} />,
};

export const LoadFailed: Story = {
  render: () => (
    <Screen {...MANAGED_DOMAINS} domains={[]} error={{ message: "upstream timed out" }} />
  ),
};

/** The Not active view: every zone short of active, one without a provisioning cluster. */
export const NotProvisioned: Story = {
  render: () => (
    <Screen
      {...MANAGED_DOMAINS}
      domains={[DOMAIN_ACTIVE, DOMAIN_PROVISIONING, DOMAIN_UNPROVISIONED]}
      initial={{ view: "pending" }}
    />
  ),
};

/** A revalidate and a delete in flight: the row actions are disabled. */
export const Busy: Story = {
  render: () => <Screen {...MANAGED_DOMAINS} revalidating deleting />,
};

export const LongStrings: Story = {
  render: () => <Screen {...MANAGED_DOMAINS} domains={[DOMAIN_LONG, DOMAIN_ACTIVE]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen {...MANAGED_DOMAINS} domains={[DOMAIN_LONG, DOMAIN_ACTIVE]} />
    </div>
  ),
};
