import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PodExpander, type PodExpanderProps } from "@/components/observability/PodExpander";
import type { PodResourceUsage } from "@/components/observability/use-pod-resource-usage";

const meta: Meta = { title: "Patterns/Observability/PodExpander" };
export default meta;

type Story = StoryObj;

const START = Date.parse("2026-09-28T12:00:00Z");

const USAGE: PodResourceUsage = {
  podName: "checkout-web-7d9f8b6c4-x2k9p",
  rangeSeconds: 3600,
  reason: "OK",
  restartCount: 3,
  lastRestartAt: "2026-09-28T11:42:00Z",
  samples: Array.from({ length: 24 }, (_, i) => ({
    ts: new Date(START + i * 150_000).toISOString(),
    cpuCores: 0.25 + Math.sin(i / 3) * 0.1,
    memoryBytes: 3.1e8 + Math.cos(i / 4) * 4e7,
  })),
};

const base: PodExpanderProps = {
  appSlug: "checkout",
  podName: USAGE.podName,
  defaultContainer: "web",
  fallbackRestartCount: 3,
  usage: USAGE,
  loading: false,
  onRetry: () => {},
};

export const WithUsage: Story = { render: () => <PodExpander {...base} /> };

export const Loading: Story = {
  render: () => <PodExpander {...base} usage={null} loading />,
};

export const NoSamplesYet: Story = {
  render: () => <PodExpander {...base} usage={{ ...USAGE, reason: "NO_DATA_YET", samples: [] }} />,
};

export const NotConfigured: Story = {
  render: () => (
    <PodExpander {...base} usage={{ ...USAGE, reason: "NOT_CONFIGURED", samples: [] }} />
  ),
};

export const QueryError: Story = {
  render: () => <PodExpander {...base} usage={{ ...USAGE, reason: "ERROR", samples: [] }} />,
};

/** No answer from the per-pod query; the table row's count stands in. */
export const FallbackRestarts: Story = {
  render: () => (
    <PodExpander {...base} usage={null} fallbackRestartCount={1} defaultContainer={null} />
  ),
};
