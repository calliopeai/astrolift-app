import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { InviteSheet } from "./InviteDialog";
import {
  BINDINGS,
  GROUPS,
  INVITATIONS,
  LONG_BINDINGS,
  LONG_GROUPS,
  LONG_INVITATIONS,
  LONG_MEMBERS,
  MEMBERS,
  type PeopleData,
  RESOLVED_INVITATIONS,
  inviteProps,
  membersProps,
} from "./members.fixtures";
import { MembersScreen, type MembersScreenProps } from "./MembersScreen";
import { PEOPLE_LIST } from "./people-model";

const meta: Meta = {
  title: "Screens/Members/MembersScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The screen over the rows the server would answer the view with. */
function People({
  initial,
  data,
  ...overrides
}: Partial<Omit<MembersScreenProps, "list">> & {
  initial?: Partial<ListState>;
  data?: PeopleData;
}) {
  const list = useLocalListState(PEOPLE_LIST, initial);
  return <MembersScreen list={list} {...membersProps(list, data, overrides)} />;
}

/** Everyone in the org, their roles and teams, with the invite sheet wired (closed until clicked). */
export const Full: Story = {
  render: () => <People renderInviteDialog={(p) => <InviteSheet {...inviteProps()} {...p} />} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("grace@example.com")).toBeInTheDocument();
    // Roles come from the page's bindings, teams from the member row.
    await expect(canvas.getAllByText("org_admin").length).toBeGreaterThan(0);
    await expect(canvas.getAllByText("platform").length).toBeGreaterThan(0);
  },
};

/** More people than a page: the numbers are the server's count. */
export const ManyPages: Story = {
  render: () => <People totalCount={312} initial={{ page: 3 }} />,
};

export const Loading: Story = {
  render: () => <People data={{ members: [], bindings: [] }} loading />,
};

export const Empty: Story = {
  render: () => <People data={{ members: [], bindings: [], invitations: [], groups: [] }} />,
};

export const NoMatches: Story = {
  render: () => <People data={{ members: [] }} initial={{ q: "nobody" }} />,
};

export const LoadFailed: Story = {
  render: () => (
    <People
      data={{ members: [], bindings: [] }}
      error={{ message: "upstream timed out after 30s (identity.membersPage)" }}
    />
  ),
};

/** Mine: people who share a team with the viewer (ada is on platform with linus). */
export const Mine: Story = {
  render: () => (
    <People
      data={{ members: MEMBERS.filter((m) => m.teams?.some((t) => t.slug === "platform")) }}
      initial={{ view: "mine" }}
    />
  ),
};

/** The Invited view: pending invitations with resend and revoke. */
export const Invited: Story = {
  render: () => <People initial={{ view: "invited" }} />,
};

/** Invitation history is a status chip on the Invited view. */
export const InvitationHistory: Story = {
  render: () => <People initial={{ view: "invited", filters: { status: "accepted" } }} />,
};

export const InvitedEmpty: Story = {
  render: () => <People data={{ invitations: [] }} initial={{ view: "invited" }} />,
};

/** IdP groups the org knows, with their members, grants and mappings. */
export const Groups: Story = {
  render: () => <People initial={{ view: "groups" }} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByText(/12 members/).length).toBeGreaterThan(0);
  },
};

export const GroupsEmpty: Story = {
  render: () => <People data={{ groups: [] }} initial={{ view: "groups" }} />,
};

/** Holders of an org role that can manage members. */
export const Admins: Story = {
  render: () => (
    <People
      data={{ members: MEMBERS.filter((m) => ["ada", "grace"].includes(m.user.username)) }}
      initial={{ view: "admins" }}
    />
  ),
};

/** Filter chips: a role and a last-active window. */
export const Filtered: Story = {
  render: () => (
    <People
      data={{ members: MEMBERS.filter((m) => m.user.username === "grace") }}
      initial={{ filters: { role: "app-operator", active: "7d" } }}
    />
  ),
};

/** A viewer without org.manage_members: no row menus. */
export const NoManageAccess: Story = {
  render: () => <People canManageMembers={false} />,
};

export const LongStrings: Story = {
  render: () => (
    <People
      data={{
        members: [...LONG_MEMBERS, ...MEMBERS],
        bindings: [...LONG_BINDINGS, ...BINDINGS],
        invitations: [...LONG_INVITATIONS, ...INVITATIONS, ...RESOLVED_INVITATIONS],
      }}
    />
  ),
};

export const LongGroups: Story = {
  render: () => (
    <People data={{ groups: [...LONG_GROUPS, ...GROUPS] }} initial={{ view: "groups" }} />
  ),
};

export const LongInvitations: Story = {
  render: () => (
    <People
      data={{ invitations: [...LONG_INVITATIONS, ...INVITATIONS] }}
      initial={{ view: "invited" }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <People
        data={{
          members: [...LONG_MEMBERS, ...MEMBERS],
          bindings: [...LONG_BINDINGS, ...BINDINGS],
        }}
      />
    </div>
  ),
};
