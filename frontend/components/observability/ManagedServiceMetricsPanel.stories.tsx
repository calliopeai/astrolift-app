import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  ManagedServiceMetricsList,
  ManagedServiceMetricsPanel,
  type ManagedServiceMetricsPanelProps,
} from "@/components/observability/ManagedServiceMetricsPanel";
import type { TimeRangeKey } from "@/components/observability/golden-signals-types";
import type {
  AstroliftManagedServiceMetrics,
  AstroliftTimeSeriesPoint,
} from "@/graphql/__generated__/schema";

const meta: Meta = { title: "Patterns/Observability/ManagedServiceMetricsPanel" };
export default meta;

type Story = StoryObj;

const START = Date.parse("2026-09-28T12:00:00Z");

const samples = (base: number, swing: number): AstroliftTimeSeriesPoint[] =>
  Array.from({ length: 24 }, (_, i) => ({
    ts: new Date(START + i * 150_000).toISOString(),
    value: Math.max(0, base + Math.sin(i / 3) * swing),
  }));

const series = (name: string, unit: string, base: number, swing: number) => ({
  name,
  unit,
  source: "cloudwatch",
  samples: samples(base, swing),
});

const POSTGRES: AstroliftManagedServiceMetrics = {
  kind: "postgres",
  managedServiceId: "ms-pg",
  name: "checkout-db",
  rangeSeconds: 3600,
  series: [
    series("connections", "count", 24, 6),
    series("cpu", "ratio", 0.35, 0.15),
    series("iops", "count", 480, 120),
    series("slow_queries", "count", 2, 2),
    series("replica_lag", "seconds", 0.4, 0.3),
  ],
};

const OBJECT_STORE: AstroliftManagedServiceMetrics = {
  kind: "object_store",
  managedServiceId: "ms-s3",
  name: "checkout-assets",
  rangeSeconds: 3600,
  series: [
    series("bucket_size", "bytes", 3.2e10, 1e8),
    series("request_count", "rps", 12, 4),
    series("egress_bytes", "bytes", 5e7, 2e7),
  ],
};

const MODEL: AstroliftManagedServiceMetrics = {
  kind: "model_endpoint",
  managedServiceId: "ms-llm",
  name: "qwen3-32b-instruct",
  rangeSeconds: 3600,
  series: [
    series("tokens_per_second", "rps", 820, 200),
    series("requests_running", "count", 6, 3),
    series("requests_waiting", "count", 1, 1),
    series("kv_cache_usage", "ratio", 0.62, 0.15),
  ],
};

/** Holds the range the way useManagedServiceMetrics does in the app. */
function Controlled(props: Omit<ManagedServiceMetricsPanelProps, "range" | "onRangeChange">) {
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  return <ManagedServiceMetricsPanel {...props} range={range} onRangeChange={setRange} />;
}

export const Postgres: Story = { render: () => <Controlled data={POSTGRES} loading={false} /> };

export const ObjectStore: Story = {
  render: () => <Controlled data={OBJECT_STORE} loading={false} />,
};

export const ModelEndpoint: Story = { render: () => <Controlled data={MODEL} loading={false} /> };

export const Loading: Story = { render: () => <Controlled data={null} loading /> };

export const NoMetricsYet: Story = {
  render: () => <Controlled data={{ ...POSTGRES, series: [] }} loading={false} />,
};

const BY_ID: Record<string, AstroliftManagedServiceMetrics> = {
  "ms-pg": POSTGRES,
  "ms-s3": OBJECT_STORE,
  "ms-llm": MODEL,
};

/** Only the supported kinds get a panel; the redis binding is skipped. */
export const List: Story = {
  render: () => (
    <ManagedServiceMetricsList
      managedServices={[
        { id: "ms-pg", kind: "postgres" },
        { id: "ms-redis", kind: "redis" },
        { id: "ms-llm", kind: "model_endpoint" },
      ]}
      renderPanel={(id) => <Controlled data={BY_ID[id] ?? null} loading={false} />}
    />
  ),
};
