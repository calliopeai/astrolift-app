import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { ROLE_BINDINGS } from "./fixtures";
import { GroupDetail, GroupMembersPanel } from "./GroupDetail";
import { accessProps } from "./principal.fixtures";
import { ACCESS_LIST } from "./principal-access";
import { PrincipalAccessPanel } from "./PrincipalAccessPanel";

const meta: Meta = {
  title: "Screens/Administration/Access/GroupDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const GROUP = "okta:platform-admins";
const LONG_GROUP =
  "azure_ad:emea-regional-compliance-and-release-coordination-group-0001-with-a-long-suffix";

const groupBindings = ROLE_BINDINGS.slice(0, 3).map((b) => ({
  ...b,
  user: null,
  groupExternalId: GROUP,
}));

const NOTE =
  "The resolver does not match group grants yet, so these give members nothing until it does.";

function Access({ bindings = groupBindings }: { bindings?: typeof groupBindings }) {
  const list = useLocalListState(ACCESS_LIST);
  return <PrincipalAccessPanel list={list} {...accessProps(list, bindings, { note: NOTE })} />;
}

export const AccessTab: Story = {
  render: () => (
    <GroupDetail externalId={GROUP} tab="access">
      <Access />
    </GroupDetail>
  ),
};

export const MembersTab: Story = {
  render: () => (
    <GroupDetail externalId={GROUP} tab="members">
      <GroupMembersPanel externalId={GROUP} />
    </GroupDetail>
  ),
};

export const NoGrants: Story = {
  render: () => (
    <GroupDetail externalId={GROUP} tab="access">
      <Access bindings={[]} />
    </GroupDetail>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <GroupDetail externalId={LONG_GROUP} tab="members">
      <GroupMembersPanel externalId={LONG_GROUP} />
    </GroupDetail>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <GroupDetail externalId={LONG_GROUP} tab="access">
        <Access />
      </GroupDetail>
    </div>
  ),
};
