import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { pageData } from "@/components/screens/administration/access/fixtures";

import { GrantRoleSheet } from "./GrantRoleDialog";
import { InviteSheet } from "./InviteDialog";
import { MEMBERS_LIST } from "./members-list";
import {
  BINDINGS,
  INVITATIONS,
  LONG_BINDINGS,
  LONG_INVITATIONS,
  LONG_MEMBERS,
  MEMBERS,
  RESOLVED_INVITATIONS,
  grantRoleProps,
  inviteProps,
  membersProps,
} from "./members.fixtures";
import { MembersScreen, type MembersScreenProps } from "./MembersScreen";

const meta: Meta = {
  title: "Screens/Members/MembersScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Members({
  initial,
  ...props
}: Omit<MembersScreenProps, "list"> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(MEMBERS_LIST, initial);
  return <MembersScreen list={list} {...props} />;
}

/** People, with the grant and invite sheets wired to fixtures (closed until clicked). */
export const Full: Story = {
  render: () => (
    <Members
      {...membersProps()}
      renderGrantDialog={(p) => <GrantRoleSheet {...grantRoleProps()} {...p} />}
      renderInviteDialog={(p) => <InviteSheet {...inviteProps()} {...p} />}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <Members
      {...membersProps({
        people: pageData([], { loading: true }),
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
  render: () => <Members {...membersProps({ people: pageData([]), bindingIndexRows: [] })} />,
};

export const NoMatches: Story = {
  render: () => <Members {...membersProps({ people: pageData([]) })} initial={{ q: "nobody" }} />,
};

export const LoadFailed: Story = {
  render: () => (
    <Members
      {...membersProps({
        people: pageData([], { error: { message: "upstream timed out after 30s" } }),
      })}
    />
  ),
};

/** The Invited view: pending invitations with resend and revoke. */
export const Invited: Story = {
  render: () => <Members {...membersProps()} initial={{ view: "invited" }} />,
};

export const InvitedEmpty: Story = {
  render: () => (
    <Members {...membersProps({ invitations: pageData([]) })} initial={{ view: "invited" }} />
  ),
};

/** Invitation history: accepted, revoked and expired rows with their delete action. */
export const InvitationHistory: Story = {
  render: () => (
    <Members
      {...membersProps({ invitations: pageData([...INVITATIONS, ...RESOLVED_INVITATIONS]) })}
      initial={{ view: "invitations" }}
    />
  ),
};

export const RoleBindings: Story = {
  render: () => <Members {...membersProps()} initial={{ view: "bindings" }} />,
};

/** Two bindings selected: the bulk revoke action shows. */
export const WithSelection: Story = {
  render: () => <Members {...membersProps()} initial={{ view: "bindings" }} />,
  play: async ({ canvasElement }) => {
    const boxes = within(canvasElement).getAllByRole("checkbox");
    await userEvent.click(boxes[2]);
    await userEvent.click(boxes[4]);
    await expect(within(canvasElement).getByRole("button", { name: /Revoke 2/ })).toBeVisible();
  },
};

/** A viewer without org.manage_members: no row menus, no selection column. */
export const NoManageAccess: Story = {
  render: () => (
    <Members {...membersProps({ canManageMembers: false })} initial={{ view: "bindings" }} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Members
      {...membersProps({
        people: pageData([...LONG_MEMBERS, ...MEMBERS], { totalCount: 1_284, nextCursor: "c2" }),
        bindingIndexRows: [...LONG_BINDINGS, ...BINDINGS],
      })}
    />
  ),
};

export const LongInvitations: Story = {
  render: () => (
    <Members
      {...membersProps({ invitations: pageData([...LONG_INVITATIONS, ...INVITATIONS]) })}
      initial={{ view: "invited" }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Members
        {...membersProps({
          people: pageData([...LONG_MEMBERS, ...MEMBERS]),
          bindingIndexRows: [...LONG_BINDINGS, ...BINDINGS],
        })}
      />
    </div>
  ),
};
