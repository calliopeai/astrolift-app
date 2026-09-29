import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Sparkline } from "@/components/viz/sparkline";

const meta: Meta<typeof Sparkline> = { title: "Patterns/Viz/Sparkline", component: Sparkline };
export default meta;
const DATA = [3, 5, 4, 8, 6, 9, 12, 10, 14];
export const Line: StoryObj<typeof Sparkline> = { args: { data: DATA, ariaLabel: "p95 latency" } };
export const Area: StoryObj<typeof Sparkline> = {
  args: { data: DATA, variant: "area", ariaLabel: "Requests" },
};
export const Flat: StoryObj<typeof Sparkline> = {
  args: { data: [0, 0, 0, 0], ariaLabel: "No traffic" },
};
