import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { MiniBar } from "@/components/viz/mini-bar";

const meta: Meta<typeof MiniBar> = { title: "Patterns/Viz/MiniBar", component: MiniBar };
export default meta;
export const Default: StoryObj<typeof MiniBar> = {
  args: { data: [4, 9, 2, 6, 3, 12, 7], ariaLabel: "Failed runs per day" },
};
