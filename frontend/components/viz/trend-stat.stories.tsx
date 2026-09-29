import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { TrendStat } from "@/components/viz/trend-stat";

const meta: Meta = { title: "Patterns/Viz/TrendStat" };
export default meta;
export const States: StoryObj = {
  render: () => (
    <div className="flex gap-10">
      <TrendStat value="97%" delta={2.1} deltaLabel="vs last week" data={[91, 93, 95, 94, 97]} />
      <TrendStat value="$41" delta={-8} invert deltaLabel="spend" data={[52, 50, 46, 44, 41]} />
      <TrendStat value="212" delta={null} />
    </div>
  ),
};
