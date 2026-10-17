import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ZentinelleOverview } from "./ZentinelleOverview";
import {
  CONNECTED,
  DEGRADED_CONNECTION,
  HEALTH_COUNTS,
  HEALTH_NOT_AVAILABLE,
  HEALTHY_CONNECTION,
  OLDER_ZENTINELLE_CONNECTION,
  STALE_CONNECTION,
  UPSTREAM_ERROR,
} from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Overview",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const usage = CONNECTED.health.usage;
const counts = CONNECTED.health.counts;

export const Healthy: Story = {
  render: () => (
    <ZentinelleOverview connection={HEALTHY_CONNECTION} usage={usage} counts={counts} />
  ),
};

export const Degraded: Story = {
  render: () => (
    <ZentinelleOverview connection={DEGRADED_CONNECTION} usage={usage} counts={counts} />
  ),
};

/** A gateway Zentinelle has not heard from in 42 minutes, and one that never reported. */
export const StaleGateway: Story = {
  render: () => <ZentinelleOverview connection={STALE_CONNECTION} usage={usage} counts={counts} />,
};

/** A Zentinelle released before the health read: the tiles say so instead of failing. */
export const OlderZentinelle: Story = {
  render: () => (
    <ZentinelleOverview
      connection={OLDER_ZENTINELLE_CONNECTION}
      usage={usage}
      counts={{ data: HEALTH_NOT_AVAILABLE, loading: false, error: null }}
    />
  ),
};

export const NoBudget: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={usage}
      counts={{ data: { ...HEALTH_COUNTS, budgets: [] }, loading: false, error: null }}
    />
  ),
};

export const BudgetExceeded: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={usage}
      counts={{
        data: {
          ...HEALTH_COUNTS,
          budgets: [
            {
              id: "pol_2",
              name: "Monthly model budget for the platform reliability team's long-running agents",
              scope: "organization",
              limitUsd: 500,
              spendUsd: 538.12,
              percent: 107.62,
              level: "exceeded",
            },
          ],
        },
        loading: false,
        error: null,
      }}
    />
  ),
};

export const GatewaysOffForInstall: Story = {
  render: () => (
    <ZentinelleOverview
      connection={{ ...HEALTHY_CONNECTION, gatewayFeatureEnabled: false }}
      usage={usage}
      counts={counts}
    />
  ),
};

export const NoClusters: Story = {
  render: () => (
    <ZentinelleOverview
      connection={{ ...HEALTHY_CONNECTION, clusters: [] }}
      usage={usage}
      counts={counts}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={{ data: null, loading: true, error: null }}
      counts={{ data: null, loading: true, error: null }}
    />
  ),
};

export const UsageError: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={{ data: null, loading: false, error: UPSTREAM_ERROR }}
      counts={counts}
    />
  ),
};

export const HealthError: Story = {
  render: () => (
    <ZentinelleOverview
      connection={HEALTHY_CONNECTION}
      usage={usage}
      counts={{ data: null, loading: false, error: UPSTREAM_ERROR }}
    />
  ),
};

/** Usage only: the viewer cannot read the connection or its health. */
export const UsageOnly: Story = {
  render: () => (
    <ZentinelleOverview
      connection={null}
      usage={usage}
      counts={{ data: null, loading: false, error: null }}
    />
  ),
};
