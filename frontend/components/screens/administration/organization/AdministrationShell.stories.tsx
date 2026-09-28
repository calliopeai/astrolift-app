import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AdministrationShell } from "./AdministrationShell";
import { AdministrationSubnav } from "./AdministrationSubnav";
import { subnav } from "./fixtures";

const meta: Meta<typeof AdministrationShell> = {
  title: "Screens/Administration/Organization/AdministrationShell",
  component: AdministrationShell,
  parameters: { layout: "fullscreen" },
  args: {
    subnav: <AdministrationSubnav {...subnav} />,
    children: "Section content renders here.",
  },
};
export default meta;

type Story = StoryObj<typeof AdministrationShell>;

export const Full: Story = {};

export const Loading: Story = {
  args: { subnav: <AdministrationSubnav {...subnav} permissionsEnabled={false} /> },
};

export const Empty: Story = { args: { children: null } };

export const Error: Story = { args: { children: "This section failed to load." } };

export const LongStrings: Story = {
  args: {
    children:
      "Section content for Intergalactic Heavy Industries Consolidated Holdings and Subsidiary Launch Operations Worldwide ".repeat(
        6
      ),
  },
};
