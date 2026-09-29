import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { BuildScreen } from "./BuildScreen";

/** Static "coming soon" gateway: no data, so one state only. */
const meta: Meta = {
  title: "Screens/Dashboard/BuildScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

export const Full: StoryObj = { render: () => <BuildScreen /> };
