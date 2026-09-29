import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ProfileIdentity } from "./ProfileIdentity";
import { IDENTITY, LONG_PROFILE, PROFILE } from "./settings-shell-account.fixtures";

const meta: Meta = { title: "Screens/Settings/Account/ProfileIdentity" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <ProfileIdentity {...IDENTITY} /> };

export const Loading: Story = {
  render: () => <ProfileIdentity {...IDENTITY} profile={null} loading />,
};

/**
 * No profile came back. The card has no separate error state: a failed
 * query also lands here, as does a signed-out session.
 */
export const NoProfile: Story = {
  render: () => <ProfileIdentity {...IDENTITY} profile={null} />,
};

/** The identity provider owns the email; name fields stay editable. */
export const IdpLocked: Story = {
  render: () => <ProfileIdentity {...IDENTITY} profile={{ ...PROFILE, lockedFields: ["email"] }} />,
};

/** The org disables profile edits: read-only, no save. */
export const OrgDisabled: Story = {
  render: () => <ProfileIdentity {...IDENTITY} profile={{ ...PROFILE, orgAllowsEdit: false }} />,
};

export const Saving: Story = { render: () => <ProfileIdentity {...IDENTITY} saving /> };

export const LongStrings: Story = {
  render: () => <ProfileIdentity {...IDENTITY} profile={LONG_PROFILE} />,
};
