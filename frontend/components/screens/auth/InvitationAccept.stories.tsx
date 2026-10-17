import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import {
  INVITATION,
  INVITATION_SUMMARY,
  INVITATION_SUMMARY_LONG,
  LONG_TEXT,
} from "../shell/root-bare-login.fixtures";

import { InvitationAccept } from "./InvitationAccept";

/**
 * No empty state: the page is always about one invitation token. Each story
 * is one step of taking it up, as the public preview and the session decide.
 */
const meta: Meta<typeof InvitationAccept> = {
  title: "Screens/Auth/InvitationAccept",
  component: InvitationAccept,
  args: {
    ...INVITATION,
    onSignIn: fn(),
    onAccept: fn(),
    onSignOut: fn(),
    onRetry: fn(),
    onGoToDashboard: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof InvitationAccept>;

/** Signed out: who invited them to what, and signing in is the one action. */
export const SignedOut: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Acme Robotics")).toBeInTheDocument();
    await expect(canvas.getByText("Ada Lovelace")).toBeInTheDocument();
    await expect(canvas.getByText("Developer")).toBeInTheDocument();
    await expect(canvas.queryByRole("button", { name: "Accept invitation" })).toBeNull();
    await userEvent.click(canvas.getByRole("button", { name: "Sign in to accept" }));
    await expect(args.onSignIn).toHaveBeenCalled();
  },
};

/** Signed in as the invitee. */
export const ReadyToAccept: Story = {
  args: {
    view: { kind: "accept", invitation: INVITATION_SUMMARY, accepting: false, errorMessage: null },
  },
  play: async ({ canvasElement, args }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Accept invitation" }));
    await expect(args.onAccept).toHaveBeenCalled();
  },
};

/** The accept mutation in flight. */
export const Accepting: Story = {
  args: {
    view: { kind: "accept", invitation: INVITATION_SUMMARY, accepting: true, errorMessage: null },
  },
};

/** The accept failed and the invitation is still pending: the server's reason. */
export const AcceptFailed: Story = {
  args: {
    view: {
      kind: "accept",
      invitation: INVITATION_SUMMARY,
      accepting: false,
      errorMessage: "you are already a member of this scope",
    },
  },
};

export const Joined: Story = {
  args: { view: { kind: "joined", invitation: INVITATION_SUMMARY } },
};

/** Signed in, but not as the address the invitation was sent to. */
export const WrongAccount: Story = {
  args: {
    view: {
      kind: "wrongAccount",
      invitation: INVITATION_SUMMARY,
      viewerEmail: "someone.else@example.com",
    },
  },
  play: async ({ canvasElement, args }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Sign out" }));
    await expect(args.onSignOut).toHaveBeenCalled();
  },
};

export const Expired: Story = {
  args: { view: { kind: "expired", invitation: INVITATION_SUMMARY } },
};

export const Revoked: Story = {
  args: { view: { kind: "revoked", invitation: INVITATION_SUMMARY } },
};

/** Accepted already; signed out it might have been them, so sign in. */
export const UsedSignedOut: Story = {
  args: { view: { kind: "used", invitation: INVITATION_SUMMARY, signedIn: false } },
};

/** Accepted already, by an account other than this browser's. */
export const UsedByAnotherAccount: Story = {
  args: { view: { kind: "used", invitation: INVITATION_SUMMARY, signedIn: true } },
};

export const NotFound: Story = { args: { view: { kind: "notFound" } } };

/** The preview could not be read. */
export const Unavailable: Story = {
  args: { view: { kind: "unavailable" } },
  play: async ({ canvasElement, args }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Try again" }));
    await expect(args.onRetry).toHaveBeenCalled();
  },
};

export const Loading: Story = { args: { view: { kind: "loading" } } };

/** An invitation without a role joins with none. */
export const NoRole: Story = {
  args: { view: { kind: "signIn", invitation: { ...INVITATION_SUMMARY, roleName: null } } },
};

export const LongStrings: Story = {
  args: {
    view: {
      kind: "accept",
      invitation: INVITATION_SUMMARY_LONG,
      accepting: false,
      errorMessage: LONG_TEXT,
    },
  },
};

export const LongWrongAccount: Story = {
  args: {
    view: {
      kind: "wrongAccount",
      invitation: INVITATION_SUMMARY_LONG,
      viewerEmail:
        "maximiliana.theodora.wolfeschlegelsteinhausenbergerdorff@another-company.example.com",
    },
  },
};
