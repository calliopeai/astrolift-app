import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_ARN, RUN_DAYS, TRAFFIC } from "./apps-agents.fixtures";
import { ChartSeries } from "./ChartSeries";

/** One series of a Home chart panel: legend swatch, figure in mono, compact chart. */
const meta: Meta<typeof ChartSeries> = {
  title: "Home/Panels/ChartSeries",
  component: ChartSeries,
  args: {
    label: "Requests",
    value: "42.10 req/s",
    hint: "now",
    tone: "primary",
    kind: "line",
    data: TRAFFIC.values,
  },
  decorators: [
    (Story) => (
      <div style={{ width: 360 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof ChartSeries>;

export const Line: Story = {};

export const Bars: Story = {
  args: {
    label: "Agent runs per day",
    value: "178",
    hint: "7 days",
    kind: "bars",
    data: RUN_DAYS.map((d) => d.runs),
  },
};

export const Danger: Story = { args: { label: "Errors", value: "1.20%", tone: "danger" } };

export const Unknown: Story = { args: { value: null, data: [] } };

export const LongStrings: Story = { args: { label: LONG_ARN, hint: "7 days" } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
