import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { IntroductionScreen } from "./IntroductionScreen";

const meta: Meta<typeof IntroductionScreen> = {
  title: "Screens/Documentation/IntroductionScreen",
  component: IntroductionScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof IntroductionScreen>;

/** Static copy with no props: this is the only state. */
export const Full: Story = {};
