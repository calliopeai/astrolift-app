import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SettingsSubnavView } from "./SettingsSubnav";

/**
 * The Settings sub-navigation. Static links, so no loading, empty or error
 * state; the stories show which link is active per path.
 */
const meta: Meta = { title: "Screens/Settings/Shell/SettingsSubnav" };
export default meta;

type Story = StoryObj;

export const Profile: Story = { render: () => <SettingsSubnavView pathname="/settings/profile" /> };

/** A nested path keeps its section active. */
export const NestedSecurity: Story = {
  render: () => <SettingsSubnavView pathname="/settings/security/devices" />,
};

/** A path outside the four sections: nothing active. */
export const NoneActive: Story = {
  render: () => <SettingsSubnavView pathname="/settings" />,
};
