import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { LONG_POLICIES, POLICIES, policiesProps } from "./fixtures";
import { POLICIES_LIST } from "./policies-list";
import { PoliciesScreen, type PoliciesScreenProps } from "./PoliciesScreen";

const meta: Meta = {
  title: "Screens/Administration/Access/PoliciesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;
type Props = Omit<PoliciesScreenProps, "list">;

function Policies({ initial, ...props }: Props & { initial?: Partial<ListState> }) {
  const list = useLocalListState(POLICIES_LIST, initial);
  return <PoliciesScreen list={list} {...props} />;
}

export const Full: Story = {
  render: () => <Policies {...policiesProps({ nextCursor: "c2", totalCount: 57 })} />,
};

export const Loading: Story = {
  render: () => <Policies {...policiesProps({ rows: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <Policies {...policiesProps({ rows: [] })} />,
};

export const EmptyFiltered: Story = {
  render: () => <Policies {...policiesProps({ rows: [] })} initial={{ q: "zzz" }} />,
};

export const Error: Story = {
  render: () => (
    <Policies
      {...policiesProps({ rows: [], error: { message: "upstream timed out after 30s" } })}
    />
  ),
};

/** A viewer without `org.update`: no New policy, no row menu. */
export const ReadOnly: Story = {
  render: () => <Policies {...policiesProps({}, { canManage: false })} />,
};

/** The second page, with rows fading while the next one loads. */
export const Stale: Story = {
  render: () => (
    <Policies
      {...policiesProps({ stale: true, nextCursor: "c3" })}
      initial={{ after: "c2", q: "deploy" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Policies {...policiesProps({ rows: [...LONG_POLICIES, ...POLICIES] })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Policies {...policiesProps({ rows: [...LONG_POLICIES, ...POLICIES] })} />
    </div>
  ),
};
