import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  GoldenSignalsPanel,
  type GoldenSignalsPanelProps,
} from "@/components/observability/GoldenSignalsPanel";
import type { TimeRangeKey } from "@/components/observability/golden-signals-types";
import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import type { StatusBreakdownWithReason } from "@/components/observability/use-golden-signals";
import type {
  AstroliftAppGoldenSignal,
  AstroliftAppNetworkMetrics,
  AstroliftAppNetworkSeries,
  AstroliftGoldenSignalMeasurement,
  AstroliftTimeSeriesPoint,
  GoldenSignalKind,
  GoldenSignalUnavailableReason,
  NetworkDirection,
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
  measurement: {
    effectiveScope: "APP_ENVIRONMENT",
    source: name.startsWith("SATURATION") ? "CADVISOR_KUBE_STATE_METRICS" : "APP_INSTRUMENTATION",
    identityBasis: name.startsWith("SATURATION") ? "NAMESPACE" : "SOURCE_LABELS",
    available: points.length > 0,
    unavailableReason: points.length ? null : "NO_DATA_YET",
    target: {
      organizationId: "019eb737-0100-7000-8000-000000000001",
      appId: "019eb737-0100-7000-8000-000000000002",
      appSlug: "checkout",
      environmentId: "019eb737-0100-7000-8000-000000000003",
      environmentName: "production",
      clusterId: "019eb737-0100-7000-8000-000000000004",
      namespace: "checkout-production",
    },
    containers: [],
    usageSamples: [],
    limitSamples: [],
  },
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

const networkSeries = (
  direction: NetworkDirection,
  points: AstroliftTimeSeriesPoint[],
  measured: Partial<AstroliftGoldenSignalMeasurement> = {}
): AstroliftAppNetworkSeries => ({
  direction,
  unit: "bytes_per_second",
  promql: `sum(rate(container_network_${direction.toLowerCase()}_bytes_total{namespace="checkout-production"}[2m]))`,
  reason: "OK",
  samples: points,
  // 24 samples 150 s apart.
  totalBytes: points.reduce((sum, point) => sum + point.value * 150, 0),
  measurement: {
    effectiveScope: "APP_ENVIRONMENT",
    source: "CADVISOR",
    identityBasis: "NAMESPACE",
    available: true,
    unavailableReason: null,
    target: SIGNALS[0].measurement!.target,
    containers: [],
    usageSamples: [],
    limitSamples: [],
    ...measured,
  },
});

const NETWORK: AstroliftAppNetworkMetrics = {
  reason: "OK",
  rangeSeconds: 3600,
  stepSeconds: 15,
  rateWindowSeconds: 120,
  sourcedAt: "2026-09-28T13:00:00Z",
  series: [
    networkSeries("RECEIVE", samples(180_000, 60_000)),
    networkSeries("TRANSMIT", samples(42_000, 15_000)),
  ],
};

/** Both directions unavailable for one reason: no line, no total. */
const unavailableNetwork = (
  reason: ObservabilityPanelReason,
  cause: GoldenSignalUnavailableReason,
  measured: Partial<AstroliftGoldenSignalMeasurement> = {}
): AstroliftAppNetworkMetrics => ({
  ...NETWORK,
  reason,
  series: NETWORK.series.map((row) => ({
    ...row,
    reason,
    samples: [],
    totalBytes: null,
    measurement: { ...row.measurement, ...measured, available: false, unavailableReason: cause },
  })),
});

const WORKLOAD_TARGET = {
  ...SIGNALS[0].measurement!.target,
  workloadId: "019eb737-0100-7000-8000-000000000005",
  workloadSlug: "api",
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
  network: NETWORK,
  networkReason: "OK",
  networkLoading: false,
  onNetworkRetry: () => {},
};

/** Holds the range the way useGoldenSignals does in the app. */
function Controlled(props: Partial<GoldenSignalsPanelProps>) {
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  return <GoldenSignalsPanel {...base} {...props} range={range} onRangeChange={setRange} />;
}

export const Full: Story = { render: () => <Controlled /> };

export const MixedAvailability: Story = {
  render: () => (
    <Controlled
      signals={SIGNALS.map((row) => ({
        ...row,
        samples: row.name === "SATURATION_MEMORY" ? [] : row.samples,
        reason: row.name === "SATURATION_MEMORY" ? "NO_DATA_YET" : row.reason,
        measurement: {
          ...row.measurement!,
          effectiveScope: "WORKLOAD",
          target: {
            ...row.measurement!.target,
            workloadId: "019eb737-0100-7000-8000-000000000005",
            workloadSlug: "api",
          },
          available: row.name !== "SATURATION_MEMORY",
          unavailableReason: row.name === "SATURATION_MEMORY" ? "MISSING_LIMITS" : null,
        },
      }))}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <Controlled
      signals={null}
      reason={null}
      loading
      statusBreakdown={null}
      statusLoading
      network={null}
      networkReason={null}
      networkLoading
    />
  ),
};

export const NoDataYet: Story = {
  render: () => (
    <Controlled
      reason="NO_DATA_YET"
      signals={SIGNALS.map((s) => ({
        ...s,
        reason: "NO_DATA_YET",
        samples: [],
        measurement: {
          ...s.measurement!,
          available: false,
          unavailableReason: "NO_DATA_YET",
        },
      }))}
      statusBreakdown={{ ...BREAKDOWN, reason: "NO_DATA_YET", series: [] }}
      network={unavailableNetwork("NO_DATA_YET", "NO_DATA_YET")}
      networkReason="NO_DATA_YET"
    />
  ),
};

/** A workload's verified pods: provenance names each physical container. */
export const NetworkVerifiedWorkload: Story = {
  render: () => (
    <Controlled
      network={{
        ...NETWORK,
        series: NETWORK.series.map((row) => ({
          ...row,
          measurement: {
            ...row.measurement,
            effectiveScope: "WORKLOAD",
            identityBasis: "VERIFIED_RUNTIME",
            target: WORKLOAD_TARGET,
            membershipObservedAt: "2026-09-28T13:00:00Z",
            measurementStart: "2026-09-28T12:12:00Z",
            containers: [
              {
                podName: "api-7d9f8c6b5-xk2pq",
                podUid: "019eb737-0100-7000-8000-000000000006",
                containerName: "api",
                containerId: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
              },
            ],
          },
        })),
      }}
    />
  ),
};

/** Sent failed to read; received still draws, sent shows its reason. */
export const NetworkOneDirectionFailed: Story = {
  render: () => (
    <Controlled
      network={{
        ...NETWORK,
        series: [
          NETWORK.series[0],
          {
            ...NETWORK.series[1],
            reason: "ERROR",
            samples: [],
            totalBytes: null,
            measurement: {
              ...NETWORK.series[1].measurement,
              available: false,
              unavailableReason: "QUERY_ERROR",
            },
          },
        ],
      }}
    />
  ),
};

/** A replaced workload: refused, never the namespace total. */
export const NetworkOwnershipUnverified: Story = {
  render: () => (
    <Controlled
      network={unavailableNetwork("ERROR", "OWNERSHIP_UNVERIFIED", {
        effectiveScope: "WORKLOAD",
        identityBasis: "UNRESOLVED",
        target: WORKLOAD_TARGET,
      })}
      networkReason="ERROR"
    />
  ),
};

export const NetworkNotConfigured: Story = {
  render: () => (
    <Controlled
      network={unavailableNetwork("NOT_CONFIGURED", "NOT_CONFIGURED")}
      networkReason="NOT_CONFIGURED"
    />
  ),
};

export const NetworkNotSupportedByProvider: Story = {
  render: () => (
    <Controlled
      network={unavailableNetwork("NOT_SUPPORTED_BY_PROVIDER", "NOT_SUPPORTED_BY_PROVIDER", {
        effectiveScope: "WORKLOAD",
        identityBasis: "UNRESOLVED",
        target: WORKLOAD_TARGET,
      })}
      networkReason="NOT_SUPPORTED_BY_PROVIDER"
    />
  ),
};

/** The query itself failed: nothing to describe, a retry. */
export const NetworkReadFailed: Story = {
  render: () => <Controlled network={null} networkReason="ERROR" />,
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
