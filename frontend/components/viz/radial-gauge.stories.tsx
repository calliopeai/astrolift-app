import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RadialGauge } from "@/components/viz/radial-gauge";

const meta: Meta = { title: "Patterns/Viz/RadialGauge" };
export default meta;
export const Values: StoryObj = {
  render: () => (
    <div className="flex gap-6">
      {[0, 38, 62, 91, 100].map((v) => (
        <RadialGauge key={v} value={v} label={`${v}%`} ariaLabel={`Memory ${v}%`} />
      ))}
    </div>
  ),
};
