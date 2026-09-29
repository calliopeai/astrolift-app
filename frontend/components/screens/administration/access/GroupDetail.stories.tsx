import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { ROLE_BINDINGS } from "./fixtures";
import { GroupDetail, GroupMembersPanel } from "./GroupDetail";
import { GROUP_MAPPINGS_LIST } from "./group-mappings";
import { GroupMappingsPanel } from "./GroupMappingsPanel";
import { accessProps, mappingsProps } from "./principal.fixtures";
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

/** The Access tab as the route mounts it: the group's grants, then its role mappings. */
function Access({
  bindings = groupBindings,
  mapped = true,
}: {
  bindings?: typeof groupBindings;
  mapped?: boolean;
}) {
  const list = useLocalListState(ACCESS_LIST);
  const mappingsList = useLocalListState(GROUP_MAPPINGS_LIST);
  return (
    <>
      <PrincipalAccessPanel list={list} {...accessProps(list, bindings)} />
      <GroupMappingsPanel
        list={mappingsList}
        {...mappingsProps(mapped ? { externalId: GROUP } : { externalId: GROUP, rows: [] })}
      />
    </>
  );
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
      <Access bindings={[]} mapped={false} />
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
