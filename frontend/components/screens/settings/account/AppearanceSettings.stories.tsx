import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

/** Palette option A: a custom accent in use, with its contrast against the ground. */
export const CustomAccent: Story = {
  render: () => (
    <AppearanceSettings
      {...APPEARANCE}
      appearance={{ ...APPEARANCE.appearance, accent: "#d94f8a" }}
    />
  ),
};

/** A custom accent too faint for the ground is refused, and the reason given. */
export const CustomAccentTooFaint: Story = {
  render: () => <AppearanceSettings {...APPEARANCE} />,
  play: async ({ canvasElement }) => {
    const input = within(canvasElement).getByLabelText("Custom accent");
    await userEvent.clear(input);
    await userEvent.type(input, "#101010");
    await expect(within(canvasElement).getByText(/Too faint on Black/)).toBeInTheDocument();
  },
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <AppearanceSettings
        {...APPEARANCE}
        appearance={{ ...APPEARANCE.appearance, accent: "#d94f8a" }}
      />
    </div>
  ),
};
