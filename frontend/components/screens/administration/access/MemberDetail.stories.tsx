import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { LONG_MEMBER, LONG_ROLE_BINDINGS, MEMBER_DETAIL, ROLE_BINDINGS } from "./fixtures";
import { MemberDetail } from "./MemberDetail";
import { PersonActivityPanel } from "./PersonActivityPanel";
import { PersonTeamsPanel } from "./PersonTeamsPanel";
import { accessProps, activityProps, teamsProps } from "./principal.fixtures";
import { ACCESS_LIST } from "./principal-access";
import { PrincipalAccessPanel } from "./PrincipalAccessPanel";
import { PERSON_TEAMS_LIST } from "./use-person-teams";

const meta: Meta = {
  title: "Screens/Administration/Access/MemberDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Access({ bindings = ROLE_BINDINGS }: { bindings?: typeof ROLE_BINDINGS }) {
  const list = useLocalListState(ACCESS_LIST);
  return <PrincipalAccessPanel list={list} {...accessProps(list, bindings)} />;
}

function Teams() {
  const list = useLocalListState(PERSON_TEAMS_LIST);
  return <PersonTeamsPanel list={list} {...teamsProps(list)} />;
}

/** The Access tab: what they hold by scope, widest first. */
export const Full: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} tab="access">
      <Access />
    </MemberDetail>
  ),
};

export const TeamsTab: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} tab="teams">
      <Teams />
    </MemberDetail>
  ),
};

export const ActivityTab: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} tab="activity">
      <PersonActivityPanel {...activityProps()} />
    </MemberDetail>
  ),
};

export const Loading: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} member={null} loading tab="access">
      <Access />
    </MemberDetail>
  ),
};

/** No such member, or one who left. */
export const NotFound: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} member={null} tab="access">
      <Access />
    </MemberDetail>
  ),
};

export const Error: Story = {
  render: () => (
    <MemberDetail
      {...MEMBER_DETAIL}
      member={null}
      error={{ message: "upstream timed out after 30s (identity.members)" }}
      tab="access"
    >
      <Access />
    </MemberDetail>
  ),
};

/** A viewer without org.manage_members: no Grant access, no Remove. */
export const ReadOnly: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} canManage={false} tab="access">
      <Access />
    </MemberDetail>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <MemberDetail {...MEMBER_DETAIL} member={LONG_MEMBER} tab="access">
      <Access bindings={[...LONG_ROLE_BINDINGS, ...ROLE_BINDINGS]} />
    </MemberDetail>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <MemberDetail {...MEMBER_DETAIL} member={LONG_MEMBER} tab="access">
        <Access bindings={[...LONG_ROLE_BINDINGS, ...ROLE_BINDINGS]} />
      </MemberDetail>
    </div>
  ),
};
