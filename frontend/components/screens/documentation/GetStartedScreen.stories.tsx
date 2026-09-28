import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { GetStartedScreen } from "./GetStartedScreen";

const meta: Meta<typeof GetStartedScreen> = {
  title: "Screens/Documentation/GetStartedScreen",
  component: GetStartedScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof GetStartedScreen>;

/** Static copy with no props: this is the only state. */
export const Full: Story = {};
