import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SparklineCell } from "@/components/viz/sparkline-cell";

const meta: Meta<typeof SparklineCell> = {
  title: "Patterns/Viz/SparklineCell",
  component: SparklineCell,
};
export default meta;
export const Default: StoryObj<typeof SparklineCell> = {
  args: { data: [3, 5, 4, 8, 6, 9, 12], value: "118", ariaLabel: "Runs, last 7 days" },
};
