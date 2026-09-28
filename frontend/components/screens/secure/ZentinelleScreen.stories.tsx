import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleScreen } from "./ZentinelleScreen";

/**
 * A static placeholder page: no data, so no loading, empty, error or
 * long-strings states exist. The one story is the whole screen.
 */
const meta: Meta = {
  title: "Screens/Secure/ZentinelleScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ZentinelleScreen />,
};
