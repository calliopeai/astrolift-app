import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { METRICS, METRICS_LONG_APPS, NO_ROLLOUTS_METRICS } from "../ops/metrics-ops-shell.fixtures";

import { MetricsScreen } from "./MetricsScreen";

const meta: Meta<typeof MetricsScreen> = {
  title: "Screens/Metrics/MetricsScreen",
  component: MetricsScreen,
  parameters: { layout: "fullscreen" },
  args: METRICS,
};
export default meta;

type Story = StoryObj<typeof MetricsScreen>;

export const Full: Story = {};

export const Loading: Story = {
  args: { metrics: undefined, metricsLoading: true, apps: [], healthLoading: true },
};

/** No apps registered and no rollouts in the window. */
export const Empty: Story = {
  args: { metrics: NO_ROLLOUTS_METRICS, apps: [] },
};

/**
 * The screen has no error state: a failed query leaves its data undefined
 * once loading ends, so the KPIs read "—" and the apps table falls back to
 * its empty state. This is what a failure looks like.
 */
export const QueryFailed: Story = {
  args: { metrics: undefined, apps: [] },
};

export const LongStrings: Story = {
  args: { apps: METRICS_LONG_APPS },
};
