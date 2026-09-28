import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { NotFoundScreen } from "./NotFoundScreen";

/** Static page with one state: no data, loading, empty or error. */
const meta: Meta<typeof NotFoundScreen> = {
  title: "Screens/Shell/NotFoundScreen",
  component: NotFoundScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof NotFoundScreen>;

export const Default: Story = {};
