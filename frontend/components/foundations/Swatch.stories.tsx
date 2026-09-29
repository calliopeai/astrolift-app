import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Swatch, SwatchGroup } from "@/components/foundations/Swatch";

/** The swatch the Foundations palette is drawn with. */
const meta: Meta<typeof Swatch> = { title: "Foundations/Swatch", component: Swatch };
export default meta;

export const One: StoryObj<typeof Swatch> = { args: { token: "primary" } };

export const Group: StoryObj = {
  render: () => <SwatchGroup title="Status" tokens={["success", "warning", "danger", "info"]} />,
};
