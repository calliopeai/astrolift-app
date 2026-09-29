import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  houseTheme,
  houseThemeLocked,
  houseThemeSaveFails,
  houseThemeSaving,
  houseThemeUnset,
  ORG_LONG,
} from "./fixtures";
import { HouseThemeCard } from "./HouseThemeCard";

const meta: Meta<typeof HouseThemeCard> = {
  title: "Screens/Administration/Organization/HouseThemeCard",
  component: HouseThemeCard,
  args: houseTheme,
};
export default meta;

type Story = StoryObj<typeof HouseThemeCard>;

export const Full: Story = {};

/** A save in flight: the button reads "Saving…" and is disabled. */
export const Loading: Story = { args: houseThemeSaving };

/** No house theme: every axis on "Not set". */
export const Empty: Story = { args: houseThemeUnset };

/** Saving fails: the hook toasts the error and resolves false. */
export const Error: Story = { args: houseThemeSaveFails };

export const Locked: Story = { args: houseThemeLocked };

export const LongStrings: Story = { args: { ...houseTheme, org: ORG_LONG } };

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <HouseThemeCard {...{ ...houseTheme, org: ORG_LONG }} />
    </div>
  ),
};
