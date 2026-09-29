import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";

import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";

/** Chart series follow the accent (chart-1…5), status series never do. */
const meta: Meta = { title: "Atoms/Chart" };
export default meta;

const DATA = [
  { day: "Mon", ok: 120, failed: 4 },
  { day: "Tue", ok: 132, failed: 9 },
  { day: "Wed", ok: 101, failed: 2 },
  { day: "Thu", ok: 148, failed: 6 },
  { day: "Fri", ok: 160, failed: 3 },
];

const CONFIG = {
  ok: { label: "Succeeded", color: "var(--chart-1)" },
  failed: { label: "Failed", color: "var(--danger)" },
} satisfies ChartConfig;

export const Bars: StoryObj = {
  render: () => (
    <ChartContainer config={CONFIG} className="h-64 w-full max-w-xl">
      <BarChart data={DATA}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="day" tickLine={false} axisLine={false} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="ok" fill="var(--color-ok)" radius={2} />
        <Bar dataKey="failed" fill="var(--color-failed)" radius={2} />
      </BarChart>
    </ChartContainer>
  ),
};
