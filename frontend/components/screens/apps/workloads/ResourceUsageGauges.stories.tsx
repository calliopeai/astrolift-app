import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { USAGE, USAGE_OVER } from "./app-workloads.fixtures";
import { ResourceUsageGaugesView } from "./ResourceUsageGauges";

const meta: Meta<typeof ResourceUsageGaugesView> = {
  title: "Screens/Apps/Workloads/ResourceUsageGauges",
  component: ResourceUsageGaugesView,
  args: USAGE,
};
export default meta;

type Story = StoryObj<typeof ResourceUsageGaugesView>;

/** CPU near its limit (red band), memory comfortable. */
export const Full: Story = {};

export const Loading: Story = { args: { usage: null, loading: true } };

/**
 * No metrics flowing. The card has no separate error state: a failed or
 * empty query renders this same empty copy.
 */
export const Empty: Story = { args: { usage: null, loading: false } };

/** No limits set, so the gauges measure against the request and run over budget. */
export const OverRequest: Story = { args: USAGE_OVER };

/** The gauges carry no free text; the longest real values are multi-GiB, shown here. */
export const LongStrings: Story = {
  args: {
    usage: {
      ...USAGE_OVER.usage!,
      memory: {
        unit: "bytes",
        current: 987.65 * 1024 * 1024 * 1024,
        request: 512 * 1024 * 1024 * 1024,
        limit: 1024 * 1024 * 1024 * 1024,
        percentOfRequest: 192.9,
        percentOfLimit: 96.4,
      },
    },
  },
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  args: USAGE_OVER,
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
