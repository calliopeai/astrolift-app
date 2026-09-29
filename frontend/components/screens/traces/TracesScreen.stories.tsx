import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { TracesScreen } from "./TracesScreen";

const meta: Meta = {
  title: "Screens/Traces/TracesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

/** A static gateway page: no data, so no loading, empty or error state. */
export const Default: StoryObj = { render: () => <TracesScreen /> };
