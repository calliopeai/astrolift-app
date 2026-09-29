import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { canBindAt, type ScopeNode, type ScopeRef } from "./access-model";
import {
  CHECKOUT,
  DEPLOYER,
  LAZY_SCOPE_TREE,
  LONG_SCOPE_TREE,
  SCOPE_TREE,
  TEAM_DEV,
} from "./fixtures";
import { ScopePicker, type ScopePickerProps } from "./ScopePicker";

/**
 * Where a grant applies: org › teams › projects › apps, searchable, with
 * children loaded on expand and levels the role cannot bind at disabled.
 */
const meta: Meta = { title: "Access/ScopePicker", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

function Picker(props: Partial<ScopePickerProps> & { initial?: ScopeRef | null }) {
  const { initial = null, ...rest } = props;
  const [value, setValue] = React.useState<ScopeRef | null>(initial);
  return (
    <div className="max-w-md">
      <ScopePicker roots={SCOPE_TREE} value={value} onChange={setValue} {...rest} />
    </div>
  );
}

/** Opened from checkout's Access tab: its path is open and it is picked. */
export const Preselected: Story = { render: () => <Picker initial={CHECKOUT} /> };

export const NothingPicked: Story = { render: () => <Picker /> };

/** An app role: the org, teams and projects are disabled with the reason. */
export const AppRoleConstraint: Story = {
  render: () => <Picker initial={CHECKOUT} selectable={(n) => canBindAt(DEPLOYER, n.kind)} />,
};

/** A team role: teams and below. */
export const TeamRoleConstraint: Story = {
  render: () => <Picker selectable={(n) => canBindAt(TEAM_DEV, n.kind)} />,
};

const lazyChildren = (node: ScopeNode) =>
  Promise.resolve(
    SCOPE_TREE[0].children?.find((n) => n.kind === node.kind && n.id === node.id)?.children ?? []
  );

/** Teams arrive without their projects; expanding one loads them. */
export const LazyChildren: Story = {
  render: () => <Picker roots={LAZY_SCOPE_TREE} loadChildren={lazyChildren} />,
};

export const LazyChildrenFail: Story = {
  render: () => (
    <Picker
      roots={LAZY_SCOPE_TREE}
      loadChildren={() => Promise.reject(new globalThis.Error("upstream timed out"))}
    />
  ),
};

export const Loading: Story = { render: () => <Picker loading /> };

export const Error: Story = {
  render: () => <Picker error={{ message: "Network error: 502 Bad Gateway" }} onRetry={() => {}} />,
};

export const Empty: Story = { render: () => <Picker roots={[]} /> };

export const LongStrings: Story = {
  render: () => (
    <div className="max-w-xs">
      <Picker roots={LONG_SCOPE_TREE} initial={{ kind: "APP", id: "app-long", name: "x" }} />
    </div>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Picker initial={CHECKOUT} selectable={(n) => canBindAt(TEAM_DEV, n.kind)} />
    </div>
  ),
};
