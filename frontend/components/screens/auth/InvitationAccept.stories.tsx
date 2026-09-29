import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn } from "storybook/test";

import { INVITATION, LONG_TEXT } from "../shell/root-bare-login.fixtures";

import { InvitationAccept } from "./InvitationAccept";

/** No empty state: the page is always about one invitation token. */
const meta: Meta<typeof InvitationAccept> = {
  title: "Screens/Auth/InvitationAccept",
  component: InvitationAccept,
  parameters: { layout: "fullscreen" },
  args: { ...INVITATION, onAccept: fn(async () => {}), onGoToDashboard: fn() },
};
export default meta;

type Story = StoryObj<typeof InvitationAccept>;

/** Before accepting. */
export const Pending: Story = {};

/** The accept mutation in flight. */
export const Loading: Story = { args: { loading: true } };

export const Accepted: Story = { args: { ok: true } };

export const Failed: Story = {
  args: { errorMessage: "This invitation was sent to a different email address." },
};

export const LongStrings: Story = { args: { errorMessage: LONG_TEXT } };
