import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { UPTIME, UPTIME_DATA } from "./app-overview-cards-a.fixtures";
import { UptimeCardView } from "./UptimeCard";

const meta: Meta<typeof UptimeCardView> = {
  title: "Screens/Apps/Overview/UptimeCard",
  component: UptimeCardView,
  args: UPTIME,
};
export default meta;

type Story = StoryObj<typeof UptimeCardView>;

export const Full: Story = {};

export const Loading: Story = { args: { uptime: null, loading: true } };

/** No probe results yet: the neutral "monitoring is active" hint. */
export const Empty: Story = { args: { uptime: { ...UPTIME_DATA, totalChecks: 0, recent: [] } } };

/**
 * The card has no error state (a failed query reads as no data, the empty
 * hint). Closest real state that signals trouble: the app is down.
 */
export const Down: Story = {
  args: {
    uptime: {
      ...UPTIME_DATA,
      isUp: false,
      uptimePct: 71.2,
      recent: UPTIME_DATA.recent.map((p, i) => ({
        ...p,
        isUp: i > 3,
        latencyMs: i > 3 ? p.latencyMs : 5000,
      })),
    },
  },
};

/** Long values: a large uptime window and a never-rounded percentage. */
export const LongStrings: Story = {
  args: { uptime: { ...UPTIME_DATA, uptimePct: 99.99999999999, windowHours: 87600 } },
};

export const QueryError: Story = {
  args: { uptime: null, loading: false, error: "Network error: upstream timed out" },
};

export const At768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <UptimeCardView {...args} />
    </div>
  ),
};
