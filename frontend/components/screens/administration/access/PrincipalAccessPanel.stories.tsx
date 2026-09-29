import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { LONG_ROLE_BINDINGS, ROLE_BINDINGS } from "./fixtures";
import { accessProps, COVERED_BINDINGS } from "./principal.fixtures";
import { ACCESS_LIST } from "./principal-access";
import { PrincipalAccessPanel, type PrincipalAccessPanelProps } from "./PrincipalAccessPanel";

const meta: Meta = {
  title: "Screens/Administration/Access/PrincipalAccessPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Panel({
  bindings,
  initial,
  ...overrides
}: Partial<Omit<PrincipalAccessPanelProps, "list">> & {
  bindings?: typeof ROLE_BINDINGS;
  initial?: Partial<ListState>;
}) {
  const list = useLocalListState(ACCESS_LIST, initial);
  return <PrincipalAccessPanel list={list} {...accessProps(list, bindings, overrides)} />;
}

export const Full: Story = { render: () => <Panel /> };

/** A team grant also given at the org: the row says so, and Remove says they keep it. */
export const Covered: Story = { render: () => <Panel bindings={COVERED_BINDINGS} /> };

/** `can:app.deploy`: only the grants that carry the permission. */
export const CanFilter: Story = {
  render: () => <Panel initial={{ filters: { can: "app.deploy" } }} />,
};

/** A team's page: every holder at the team, with a Who column. */
export const TeamHolders: Story = {
  render: () => <Panel showHolder />,
};

export const Loading: Story = { render: () => <Panel bindings={[]} loading /> };

export const Empty: Story = { render: () => <Panel bindings={[]} /> };

export const LoadFailed: Story = {
  render: () => <Panel bindings={[]} error={{ message: "upstream timed out after 30s" }} />,
};

export const Truncated: Story = { render: () => <Panel truncated /> };

export const ReadOnly: Story = { render: () => <Panel canManage={false} /> };

export const LongStrings: Story = {
  render: () => <Panel bindings={[...LONG_ROLE_BINDINGS, ...ROLE_BINDINGS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel bindings={[...LONG_ROLE_BINDINGS, ...ROLE_BINDINGS]} showHolder />
    </div>
  ),
};
