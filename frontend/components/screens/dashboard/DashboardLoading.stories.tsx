import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DashboardLoading } from "./DashboardLoading";

/** The route-level skeleton has one state: loading. */
const meta: Meta = {
  title: "Screens/Dashboard/DashboardLoading",
  parameters: { layout: "fullscreen" },
};
export default meta;

export const Loading: StoryObj = { render: () => <DashboardLoading /> };
