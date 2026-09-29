import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  GoldenSignalsPanel,
  type GoldenSignalsPanelProps,
} from "@/components/observability/GoldenSignalsPanel";
import type { TimeRangeKey } from "@/components/observability/golden-signals-types";
import type { StatusBreakdownWithReason } from "@/components/observability/use-golden-signals";
import type {
  AstroliftAppGoldenSignal,
  AstroliftTimeSeriesPoint,
  GoldenSignalKind,
} from "@/graphql/__generated__/schema";

const meta: Meta = { title: "Patterns/Observability/GoldenSignalsPanel" };
export default meta;

type Story = StoryObj;

const START = Date.parse("2026-09-28T12:00:00Z");

const samples = (base: number, swing: number): AstroliftTimeSeriesPoint[] =>
  Array.from({ length: 24 }, (_, i) => ({
    ts: new Date(START + i * 150_000).toISOString(),
    value: Math.max(0, base + Math.sin(i / 3) * swing),
  }));

const signal = (
  name: GoldenSignalKind,
  unit: string,
  points: AstroliftTimeSeriesPoint[]
): AstroliftAppGoldenSignal => ({
  name,
  unit,
  promql: `sum(rate(http_requests_total{app="checkout"}[5m])) /* ${name} */`,
  rangeSeconds: 3600,
  reason: points.length ? "OK" : "NO_DATA_YET",
  samples: points,
});

const SIGNALS: AstroliftAppGoldenSignal[] = [
  signal("TRAFFIC", "rps", samples(42, 12)),
  signal("ERRORS", "ratio", samples(0.012, 0.008)),
  signal("LATENCY_P50", "seconds", samples(0.08, 0.02)),
  signal("LATENCY_P90", "seconds", samples(0.18, 0.05)),
  signal("LATENCY_P95", "seconds", samples(0.24, 0.06)),
  signal("LATENCY_P99", "seconds", samples(0.61, 0.2)),
  signal("SATURATION_CPU", "percent", samples(0.55, 0.25)),
  signal("SATURATION_MEMORY", "percent", samples(0.72, 0.1)),
];

const BREAKDOWN: StatusBreakdownWithReason = {
  promql: 'sum by (code) (rate(http_requests_total{app="checkout"}[5m]))',
  rangeSeconds: 3600,
  reason: "OK",
  series: [
    { codeClass: "2xx", topCodes: ["200", "204"], samples: samples(38, 10) },
    { codeClass: "4xx", topCodes: ["404", "429"], samples: samples(3, 2) },
    { codeClass: "5xx", topCodes: ["503"], samples: samples(0.5, 0.5) },
  ],
};

const base: GoldenSignalsPanelProps = {
  range: "1h",
  onRangeChange: () => {},
  signals: SIGNALS,
  reason: "OK",
  loading: false,
  onRetry: () => {},
  statusBreakdown: BREAKDOWN,
  statusLoading: false,
  onStatusRetry: () => {},
};

/** Holds the range the way useGoldenSignals does in the app. */
function Controlled(props: Partial<GoldenSignalsPanelProps>) {
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  return <GoldenSignalsPanel {...base} {...props} range={range} onRangeChange={setRange} />;
}

export const Full: Story = { render: () => <Controlled /> };

export const Loading: Story = {
  render: () => (
    <Controlled signals={null} reason={null} loading statusBreakdown={null} statusLoading />
  ),
};

export const NoDataYet: Story = {
  render: () => (
    <Controlled
      reason="NO_DATA_YET"
      signals={SIGNALS.map((s) => ({ ...s, reason: "NO_DATA_YET", samples: [] }))}
      statusBreakdown={{ ...BREAKDOWN, reason: "NO_DATA_YET", series: [] }}
    />
  ),
};

export const NotConfigured: Story = {
  render: () => <Controlled reason="NOT_CONFIGURED" signals={[]} statusBreakdown={null} />,
};

export const NotSupportedByProvider: Story = {
  render: () => (
    <Controlled reason="NOT_SUPPORTED_BY_PROVIDER" signals={[]} statusBreakdown={null} />
  ),
};

export const PanelError: Story = {
  render: () => <Controlled reason="ERROR" signals={[]} statusBreakdown={null} />,
};

export const StatusBreakdownError: Story = {
  render: () => <Controlled statusBreakdown={{ ...BREAKDOWN, reason: "ERROR", series: [] }} />,
};
