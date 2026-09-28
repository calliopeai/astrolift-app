import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AdministrationSubnav } from "./AdministrationSubnav";
import { subnav, subnavFlagOff, subnavNoMatch } from "./fixtures";

const meta: Meta<typeof AdministrationSubnav> = {
  title: "Screens/Administration/Organization/AdministrationSubnav",
  component: AdministrationSubnav,
  parameters: { layout: "fullscreen" },
  args: subnav,
};
export default meta;

type Story = StoryObj<typeof AdministrationSubnav>;

export const Full: Story = {};

/** Before the server-info flag resolves the Permissions entry stays hidden. */
export const Loading: Story = { args: { ...subnav, permissionsEnabled: false } };

/** A nested route under Members, flag off. */
export const FlagOff: Story = { args: subnavFlagOff };

/** No link matches the current path, so nothing is marked active. */
export const Empty: Story = { args: subnavNoMatch };

/** Same as Empty; the subnav has no error state of its own. */
export const Error: Story = { args: subnavNoMatch };

export const LongStrings: Story = {
  args: { ...subnav, pathname: `/administration/organization/${"nested/".repeat(20)}` },
};
