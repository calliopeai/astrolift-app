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

/**
 * The screen has no error state: a failed query leaves its data empty once
 * loading ends, so each card shows its empty state and the KPIs read zero.
 */
export const QueryFailed: Story = {
  args: { ...OPS_EMPTY, metrics: undefined },
};

export const LongStrings: Story = { args: OPS_LONG };
