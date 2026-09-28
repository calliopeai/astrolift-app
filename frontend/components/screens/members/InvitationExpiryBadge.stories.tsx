import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { InvitationExpiryBadge } from "./InvitationExpiryBadge";
import { fromNow } from "./members.fixtures";

const meta: Meta = {
  title: "Screens/Members/InvitationExpiryBadge",
};
export default meta;

type Story = StoryObj;

/** More than a day left: outlined, dashed. */
export const Days: Story = { render: () => <InvitationExpiryBadge expiresAt={fromNow(6.2)} /> };

/** Under 24h: the urgent (destructive) tint. */
export const Urgent: Story = { render: () => <InvitationExpiryBadge expiresAt={fromNow(0.2)} /> };

export const Expired: Story = { render: () => <InvitationExpiryBadge expiresAt={fromNow(-2)} /> };

/** The longest label the formatter produces, in a narrow cell. */
export const LongStrings: Story = {
  render: () => (
    <div className="w-24">
      <InvitationExpiryBadge expiresAt={fromNow(89.99)} />
    </div>
  ),
};
