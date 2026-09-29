import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppLoadingScreen } from "./AppLoadingScreen";

/** The skeleton has one state: loading. There is no data, empty, or error. */
const meta: Meta<typeof AppLoadingScreen> = {
  title: "Screens/Shell/AppLoadingScreen",
  component: AppLoadingScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof AppLoadingScreen>;

export const Loading: Story = {};
