import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LogsScreen } from "./LogsScreen";

const meta: Meta = {
  title: "Screens/Logs/LogsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

/** A static gateway page: no data, so no loading, empty or error state. */
export const Default: StoryObj = { render: () => <LogsScreen /> };
