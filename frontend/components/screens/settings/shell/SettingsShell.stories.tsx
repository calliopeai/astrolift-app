import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ProfileIdentity } from "../account/ProfileIdentity";
import { IDENTITY } from "../account/settings-shell-account.fixtures";

import { SettingsShell } from "./SettingsShell";
import { SettingsSubnavView } from "./SettingsSubnav";

/** The /settings chrome: subnav over the page. It has no data states of its own. */
const meta: Meta = {
  title: "Screens/Settings/Shell/SettingsShell",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <SettingsShell subnav={<SettingsSubnavView pathname="/settings/profile" />}>
      <ProfileIdentity {...IDENTITY} />
    </SettingsShell>
  ),
};

/** A page that renders nothing yet. */
export const EmptyPage: Story = {
  render: () => (
    <SettingsShell subnav={<SettingsSubnavView pathname="/settings/appearance" />}>
      {null}
    </SettingsShell>
  ),
};
