import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FRESHNESS } from "../new/apps-list-wizard-shell.fixtures";

import { AppFreshnessRow } from "./AppFreshnessRow";

/**
 * The per-card freshness rollup. It has no loading, empty or error state of
 * its own: a null pulse renders nothing, and NeverDeployed is the closest
 * real "empty" state.
 */
const meta: Meta = {
  title: "Screens/Apps/List/AppFreshnessRow",
};
export default meta;

type Story = StoryObj;

export const Ok: Story = { render: () => <AppFreshnessRow {...FRESHNESS.ok} /> };

/** Latest deploy failed: the "View failed deploy" link shows. */
export const Degraded: Story = { render: () => <AppFreshnessRow {...FRESHNESS.degraded} /> };

export const Stale: Story = { render: () => <AppFreshnessRow {...FRESHNESS.stale} /> };

export const NeverDeployed: Story = { render: () => <AppFreshnessRow {...FRESHNESS.never} /> };

export const LongMessage: Story = {
  render: () => (
    <AppFreshnessRow
      {...FRESHNESS.degraded}
      pulse={{
        status: "DEGRADED",
        message:
          "latest deploy failed 5m ago: ImagePullBackOff pulling registry.example.com/platform-team-shared-production-workloads/api-gateway:sha-4f2a9c1",
        ageSeconds: 300,
      }}
    />
  ),
};
