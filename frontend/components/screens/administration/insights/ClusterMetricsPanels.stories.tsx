import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PrometheusPanel, SystemMetricsPanel } from "./ClusterMetricsPanels";
import {
  PROMETHEUS,
  PROMETHEUS_LOADING,
  PROMETHEUS_LONG,
  PROMETHEUS_NO_ENDPOINT,
  PROMETHEUS_RANGE_DOWN,
  PROMETHEUS_SPARSE,
  PROMETHEUS_UNREACHABLE,
  SYSTEM,
  SYSTEM_LOADING,
  SYSTEM_NOT_SUPPORTED,
  SYSTEM_UNREACHABLE,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Insights/ClusterMetricsPanels",
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <div className="space-y-6">
      <PrometheusPanel {...PROMETHEUS} />
      <SystemMetricsPanel {...SYSTEM} />
    </div>
  ),
};

export const Loading: Story = {
  render: () => (
    <div className="space-y-6">
      <PrometheusPanel {...PROMETHEUS_LOADING} />
      <SystemMetricsPanel {...SYSTEM_LOADING} />
    </div>
  ),
};

/** No endpoint discovered yet, and a provider with no cloud-metrics integration. */
export const Empty: Story = {
  render: () => (
    <div className="space-y-6">
      <PrometheusPanel {...PROMETHEUS_NO_ENDPOINT} />
      <SystemMetricsPanel {...SYSTEM_NOT_SUPPORTED} />
    </div>
  ),
};

export const Error: Story = {
  render: () => (
    <div className="space-y-6">
      <PrometheusPanel {...PROMETHEUS_UNREACHABLE} />
      <SystemMetricsPanel {...SYSTEM_UNREACHABLE} />
    </div>
  ),
};

/** The snapshot answered but the range query did not: the one second callout. */
export const RangeUnavailable: Story = {
  render: () => <PrometheusPanel {...PROMETHEUS_RANGE_DOWN} />,
};

/** Series with no points yet, and with a single point. */
export const SparseSeries: Story = { render: () => <PrometheusPanel {...PROMETHEUS_SPARSE} /> };

export const LongStrings: Story = { render: () => <PrometheusPanel {...PROMETHEUS_LONG} /> };
