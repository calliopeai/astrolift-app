import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftRoleBinding,
} from "@/graphql/identity/identity.types";

import { GrantRoleSheet } from "./GrantRoleDialog";
import { InviteSheet } from "./InviteDialog";
import { MembersScreen } from "./MembersScreen";
import {
  BINDINGS,
  INVITATIONS,
  LONG_BINDINGS,
  LONG_INVITATIONS,
  LONG_MEMBERS,
  MEMBERS,
  RESOLVED_INVITATIONS,
  fakeSelection,
  grantRoleProps,
  inviteProps,
  membersProps,
  table,
} from "./members.fixtures";

const meta: Meta = {
  title: "Screens/Members/MembersScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Every table populated, with the grant and invite sheets wired to fixtures (closed until clicked). */
export const Full: Story = {
  render: () => (
    <MembersScreen
      {...membersProps()}
      renderGrantDialog={(p) => <GrantRoleSheet {...grantRoleProps()} {...p} />}
      renderInviteDialog={(p) => <InviteSheet {...inviteProps()} {...p} />}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        membersTable: table<AstroliftMember>({ state: "loading" }),
        bindingsTable: table<AstroliftRoleBinding>({ state: "loading" }),
        invitationsTable: table<AstroliftInvitation>({ state: "loading" }),
        bindingIndexRows: undefined,
        teams: undefined,
        projects: undefined,
        roles: undefined,
        rolesLoading: true,
      })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        membersTable: table<AstroliftMember>({ state: "empty" }),
        bindingsTable: table<AstroliftRoleBinding>({ state: "empty" }),
        invitationsTable: table<AstroliftInvitation>({ state: "empty" }),
        bindingIndexRows: [],
      })}
    />
  ),
};

/** Resolved invitations shown and none exist: the "No invitations" empty copy. */
export const EmptyAllStatuses: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        inviteStatus: null,
        invitationsTable: table<AstroliftInvitation>({ state: "empty" }),
      })}
    />
  ),
};

export const NoMatches: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        membersTable: table<AstroliftMember>({
          state: "emptyFiltered",
          search: "nobody",
          isFiltered: true,
        }),
        bindingsTable: table<AstroliftRoleBinding>({
          state: "emptyFiltered",
          search: "nobody",
          isFiltered: true,
        }),
        invitationsTable: table<AstroliftInvitation>({
          state: "emptyFiltered",
          search: "nobody",
          isFiltered: true,
        }),
      })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        membersTable: table<AstroliftMember>({
          state: "error",
          error: new globalThis.Error("upstream timed out"),
        }),
        bindingsTable: table<AstroliftRoleBinding>({
          state: "error",
          error: new globalThis.Error("upstream timed out"),
        }),
        invitationsTable: table<AstroliftInvitation>({
          state: "error",
          error: new globalThis.Error("upstream timed out"),
        }),
      })}
    />
  ),
};

/** "Show resolved" flipped: accepted / revoked / expired rows with their delete action. */
export const ShowingResolved: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        inviteStatus: null,
        invitationsTable: table<AstroliftInvitation>({
          rows: [...INVITATIONS, ...RESOLVED_INVITATIONS],
          totalCount: INVITATIONS.length + RESOLVED_INVITATIONS.length,
        }),
      })}
    />
  ),
};

/** Two bindings selected: the bulk revoke action shows. */
export const WithSelection: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        bindingSelection: fakeSelection(["b-2", "b-4"]),
      })}
    />
  ),
};

/** A viewer without org.manage_members: no selection column. */
export const NoManageAccess: Story = {
  render: () => <MembersScreen {...membersProps({ canManageMembers: false })} />,
};

export const LongStrings: Story = {
  render: () => (
    <MembersScreen
      {...membersProps({
        membersTable: table<AstroliftMember>({
          rows: [...LONG_MEMBERS, ...MEMBERS],
          totalCount: 1_284,
          hasNext: true,
        }),
        bindingsTable: table<AstroliftRoleBinding>({
          rows: [...LONG_BINDINGS, ...BINDINGS],
          totalCount: 3_907,
          hasNext: true,
        }),
        bindingIndexRows: [...LONG_BINDINGS, ...BINDINGS],
        invitationsTable: table<AstroliftInvitation>({
          rows: [...LONG_INVITATIONS, ...INVITATIONS],
          totalCount: 212,
          hasNext: true,
        }),
      })}
    />
  ),
};
