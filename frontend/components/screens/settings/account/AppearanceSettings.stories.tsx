import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppearanceSettings } from "./AppearanceSettings";
import { APPEARANCE } from "./settings-shell-account.fixtures";

/**
 * Settings › Appearance. The preference is read synchronously from local
 * storage, so the page has no loading, empty or error state; the closest
 * real states are the defaults, a custom combination, and the org lock.
 */
const meta: Meta = { title: "Screens/Settings/Account/AppearanceSettings" };
export default meta;

type Story = StoryObj;

/** The defaults: the Orbit theme, compact, 2px corners. */
export const Full: Story = { render: () => <AppearanceSettings {...APPEARANCE} /> };

/** A ground and accent that match no named theme, cards density, rounded corners. */
export const CustomCombination: Story = {
  render: () => (
    <AppearanceSettings
      {...APPEARANCE}
      appearance={{ ground: "paper", accent: "ice", density: "cards", corners: 10 }}
    />
  ),
};

/** An org policy locks the theme: read-only, no reset. */
export const Locked: Story = {
  render: () => <AppearanceSettings {...APPEARANCE} locked />,
};
