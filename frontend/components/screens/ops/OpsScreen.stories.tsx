import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { OPS, OPS_EMPTY, OPS_LOADING, OPS_LONG } from "./metrics-ops-shell.fixtures";
import { OpsScreen } from "./OpsScreen";

const meta: Meta<typeof OpsScreen> = {
  title: "Screens/Ops/OpsScreen",
  component: OpsScreen,
  parameters: { layout: "fullscreen" },
  args: OPS,
};
export default meta;

type Story = StoryObj<typeof OpsScreen>;

export const Full: Story = {};

export const Loading: Story = { args: OPS_LOADING };

/** A fresh install: no clusters, no rollouts, nothing firing, no history. */
export const Empty: Story = { args: OPS_EMPTY };

export const QueryFailed: Story = {
  args: {
    ...OPS_EMPTY,
    metrics: undefined,
    metricsError: { name: "Error", message: "Permission denied while loading this section" },
  },
};

export const LongStrings: Story = { args: OPS_LONG };
