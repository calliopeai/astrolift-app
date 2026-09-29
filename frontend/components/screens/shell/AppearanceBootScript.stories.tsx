import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppearanceBootScript } from "./AppearanceBootScript";

/**
 * Renders an inline <script> for <head>; it draws nothing. Rendered on the
 * client (as here) the script is inert, so the catalog's appearance toolbar
 * still wins. One state only.
 */
const meta: Meta<typeof AppearanceBootScript> = {
  title: "Screens/Shell/AppearanceBootScript",
  component: AppearanceBootScript,
};
export default meta;

type Story = StoryObj<typeof AppearanceBootScript>;

export const Default: Story = {};
