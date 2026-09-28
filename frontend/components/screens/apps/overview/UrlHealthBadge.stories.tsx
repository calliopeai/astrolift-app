import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { HEALTH_OK, LONG, URL_HEALTH } from "./app-overview-cards-a.fixtures";
import { UrlHealthBadgeView } from "./UrlHealthBadge";

const meta: Meta<typeof UrlHealthBadgeView> = {
  title: "Screens/Apps/Overview/UrlHealthBadge",
  component: UrlHealthBadgeView,
  args: URL_HEALTH,
};
export default meta;

type Story = StoryObj<typeof UrlHealthBadgeView>;

/** Healthy, with probe history in the tooltip (hover the pill). */
export const Full: Story = {};

/** First load, no result yet. */
export const Loading: Story = { args: { health: undefined, isInitialLoading: true, history: [] } };

/** Never probed: the neutral "Unchecked" pill, empty history. */
export const Empty: Story = { args: { health: null, history: [] } };

export const Degraded: Story = {
  args: { health: { ...HEALTH_OK, status: "degraded", statusCode: 503, latencyMs: 1204 } },
};

/** Compact mode drops the status code from a degraded pill. */
export const DegradedCompact: Story = { args: { ...Degraded.args, compact: true } };

/** The closest thing to an error: the probe reached nothing. */
export const Down: Story = {
  args: {
    health: {
      ...HEALTH_OK,
      status: "down",
      statusCode: null,
      latencyMs: null,
      message: "connection refused",
    },
  },
};

export const DownNoReason: Story = {
  args: { health: { ...HEALTH_OK, status: "down", statusCode: null, message: "" } },
};

/** A re-probe in flight after a click. */
export const Rechecking: Story = { args: { rechecking: true } };

export const LongStrings: Story = {
  args: { health: { ...HEALTH_OK, status: "down", statusCode: null, message: LONG } },
};
