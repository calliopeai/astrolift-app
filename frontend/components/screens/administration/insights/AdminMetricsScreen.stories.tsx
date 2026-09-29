import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AdminMetricsScreen } from "./AdminMetricsScreen";
import { PrometheusPanel, SystemMetricsPanel } from "./ClusterMetricsPanels";
import {
  CLUSTERS_LONG,
  METRICS,
  PROMETHEUS,
  PROMETHEUS_LOADING,
  PROMETHEUS_LONG,
  PROMETHEUS_UNREACHABLE,
  SYSTEM,
  SYSTEM_LOADING,
  SYSTEM_UNREACHABLE,
} from "./fixtures";

const meta: Meta = {
  title: "Screens/Administration/Insights/PlatformMetrics",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Connected and degraded clusters show panels; offline and never-seen show the placeholder. */
export const Full: Story = {
  render: () => (
    <AdminMetricsScreen
      {...METRICS}
      renderLivePanels={() => (
        <>
          <PrometheusPanel {...PROMETHEUS} />
          <SystemMetricsPanel {...SYSTEM} />
        </>
      )}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <AdminMetricsScreen {...METRICS} clusters={[]} loading renderLivePanels={() => null} />
  ),
};

export const Empty: Story = {
  render: () => <AdminMetricsScreen {...METRICS} clusters={[]} renderLivePanels={() => null} />,
};

/** Live clusters whose metric panels are still loading. */
export const PanelsLoading: Story = {
  render: () => (
    <AdminMetricsScreen
      {...METRICS}
      renderLivePanels={() => (
        <>
          <PrometheusPanel {...PROMETHEUS_LOADING} />
          <SystemMetricsPanel {...SYSTEM_LOADING} />
        </>
      )}
    />
  ),
};

/** The fleet list has no error state of its own; the panels carry it. */
export const Error: Story = {
  render: () => (
    <AdminMetricsScreen
      {...METRICS}
      renderLivePanels={() => (
        <>
          <PrometheusPanel {...PROMETHEUS_UNREACHABLE} />
          <SystemMetricsPanel {...SYSTEM_UNREACHABLE} />
        </>
      )}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AdminMetricsScreen
      {...METRICS}
      windowLabel="24h"
      clusters={CLUSTERS_LONG}
      renderLivePanels={() => (
        <>
          <PrometheusPanel {...PROMETHEUS_LONG} />
          <SystemMetricsPanel {...SYSTEM} />
        </>
      )}
    />
  ),
};

/** The cluster list itself failed: the error sits in the page with a Retry. */
export const ClustersError: Story = {
  render: () => (
    <AdminMetricsScreen
      {...METRICS}
      clusters={[]}
      error={new globalThis.Error("Network error: the Astrolift API did not answer.")}
      renderLivePanels={() => null}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <AdminMetricsScreen
        {...METRICS}
        clusters={CLUSTERS_LONG}
        renderLivePanels={() => (
          <>
            <PrometheusPanel {...PROMETHEUS_LONG} />
            <SystemMetricsPanel {...SYSTEM} />
          </>
        )}
      />
    </div>
  ),
};
