import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { InviteSheet } from "./InviteDialog";
import {
  INVITATION_MATCH,
  LONG_ROLE,
  MEMBER_MATCH,
  ROLES,
  fromNow,
  inviteProps,
} from "./members.fixtures";

const meta: Meta = {
  title: "Screens/Members/InviteDialog",
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <InviteSheet {...inviteProps({ email: "katherine@example.com", roleSlug: "viewer" })} />
  ),
};

/** Roles still loading and the de-dupe search in flight. */
export const Loading: Story = {
  render: () => (
    <InviteSheet
      {...inviteProps({
        email: "kat",
        searchActive: true,
        searchLoading: true,
        grantableRoles: [],
        rolesLoading: true,
      })}
    />
  ),
};

/**
 * The caller can grant no ORG role, so the form is blocked. The sheet has
 * no other error state (failures toast), so this stands in for it.
 */
export const Empty: Story = {
  render: () => <InviteSheet {...inviteProps({ grantableRoles: [], noGrantableRoles: true })} />,
};

/** The email already belongs to a member: informational only. */
export const AlreadyMember: Story = {
  render: () => (
    <InviteSheet
      {...inviteProps({ email: MEMBER_MATCH.email, searchActive: true, memberMatch: MEMBER_MATCH })}
    />
  ),
};

/** A pending invitation for the same email blocks submission until it is cancelled. */
export const InvitationExists: Story = {
  render: () => (
    <InviteSheet
      {...inviteProps({
        email: INVITATION_MATCH.email,
        searchActive: true,
        invitationMatch: INVITATION_MATCH,
        blocksSubmit: true,
      })}
    />
  ),
};

/** After a successful create: the one-time accept link. */
export const Created: Story = {
  render: () => (
    <InviteSheet
      {...inviteProps({
        created: {
          invitation: {
            id: "i-9",
            email: "katherine@example.com",
            status: "pending",
            scopeKind: "ORG",
            scopeId: "org-7f3c2a10",
            roleSlug: "viewer",
            createdAt: fromNow(0),
            expiresAt: fromNow(7),
          },
          plaintextToken: "inv_3b1f9c0e",
          acceptUrlPath: "/invitations/accept?token=inv_3b1f9c0e",
        },
        acceptUrl:
          "https://astrolift.example.com/invitations/accept?token=inv_3b1f9c0e&exp=1790000000",
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <InviteSheet
      {...inviteProps({
        email:
          "procurement-and-vendor-onboarding-shared-mailbox@emea-subsidiary-holdings.example.com",
        searchActive: true,
        memberMatch: {
          ...MEMBER_MATCH,
          email:
            "procurement-and-vendor-onboarding-shared-mailbox@emea-subsidiary-holdings.example.com",
          displayLabel: "Maximiliana Vandersloot-Oyelaran (Regional Compliance Lead)",
        },
        invitationMatch: {
          ...INVITATION_MATCH,
          email:
            "procurement-and-vendor-onboarding-shared-mailbox@emea-subsidiary-holdings.example.com",
        },
        blocksSubmit: true,
        grantableRoles: [{ ...LONG_ROLE, scopeLevel: "ORG" }, ...ROLES],
      })}
    />
  ),
};
