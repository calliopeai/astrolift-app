import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { VizGalleryScreen } from "./VizGalleryScreen";

/** Dev-only viz primitives gallery: static demo data, so one state only. */
const meta: Meta = {
  title: "Screens/Dashboard/VizGalleryScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

export const Full: StoryObj = { render: () => <VizGalleryScreen /> };
