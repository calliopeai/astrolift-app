import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { METRICS, METRICS_LONG_APPS, NO_ROLLOUTS_METRICS } from "../ops/metrics-ops-shell.fixtures";

import { METRICS_APPS_LIST } from "./metrics-apps-list";
import { MetricsScreen, type MetricsScreenProps } from "./MetricsScreen";

const meta: Meta = {
  title: "Screens/Metrics/MetricsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Screen({
  initial,
  ...over
}: Partial<MetricsScreenProps> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(METRICS_APPS_LIST, initial);
  return <MetricsScreen {...METRICS} {...over} list={list} />;
}

export const Full: Story = { render: () => <Screen /> };

export const ConfiguredTemporal: Story = {
  render: () => <Screen temporalUiUrl="https://workflows.operator.example/" />,
};

export const Loading: Story = {
  render: () => <Screen metrics={undefined} metricsLoading apps={[]} healthLoading />,
};

/** No apps registered and no rollouts in the window. */
export const Empty: Story = { render: () => <Screen metrics={NO_ROLLOUTS_METRICS} apps={[]} /> };

export const QueryFailed: Story = {
  render: () => (
    <Screen
      metrics={undefined}
      apps={[]}
      metricsError="Metrics read refused"
      healthError="App health read refused"
      onRetryMetrics={() => {}}
      onRetryHealth={() => {}}
    />
  ),
};

export const FailedRefresh: Story = {
  render: () => (
    <Screen
      metricsError="Metrics refresh refused"
      healthError="App health refresh refused"
      onRetryMetrics={() => {}}
      onRetryHealth={() => {}}
    />
  ),
};

/** The Recent failure view over the summary in hand. */
export const RecentFailure: Story = { render: () => <Screen initial={{ view: "failing" }} /> };

export const EmptyFiltered: Story = { render: () => <Screen initial={{ q: "no-such-app" }} /> };

export const LongStrings: Story = { render: () => <Screen apps={METRICS_LONG_APPS} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen apps={METRICS_LONG_APPS} />
    </div>
  ),
};
