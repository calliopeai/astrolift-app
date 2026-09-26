"use client";

/**
 * Per-managed-service metrics panel (#645 + #646).
 *
 * Renders one panel per supported managed-service binding on an app:
 *
 * * postgres → connections / cpu / iops / slow_queries / replica_lag
 * * object_store → bucket_size / request_count / errors_4xx /
 *   errors_5xx / egress_bytes
 *
 * Unsupported kinds are filtered out at the panel level — the caller
 * passes the app's full managed-service list and the panel decides
 * which to render. Cards are a 2×3 grid; loading + empty-state copy
 * mirrors the GoldenSignalsPanel exactly so the surfaces feel
 * consistent at a glance.
 */

import { useQuery } from "@apollo/client/react";
import { BrainCircuitIcon, ChartSplineIcon, DatabaseIcon, HardDriveIcon } from "lucide-react";
import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_MANAGED_SERVICE_METRICS } from "@/graphql/observability/observability.queries";
import type {
  AstroliftManagedServiceMetrics,
  AstroliftManagedServiceMetricSeries,
  AstroliftTimeSeriesPoint,
} from "@/graphql/__generated__/schema";

import { TIME_RANGE_OPTIONS, type TimeRangeKey } from "./golden-signals-types";

interface MetricsResp {
  astroliftAppManagedServiceMetrics: AstroliftManagedServiceMetrics | null;
}

/** Managed-service kinds the resolver supports today. The FE filters
 *  on this set so an app with five managed services renders panels
 *  only for the ones we can actually query. Mirrors the
 *  ``_POSTGRES_METRICS`` / ``_OBJECT_STORE_METRICS`` lists on the
 *  resolver. */
export const SUPPORTED_KINDS = new Set(["postgres", "object_store", "model_endpoint"]);

/** Per-metric display copy. Title + description are operator-facing;
 *  the metric ``name`` field on the wire matches a key here. */
const METRIC_COPY: Record<string, { title: string; description: string }> = {
  // Postgres
  connections: {
    title: "Connections",
    description: "Active client connections to this database.",
  },
  cpu: {
    title: "CPU",
    description: "Exporter process CPU usage (proxy for engine load).",
  },
  iops: {
    title: "IOPS",
    description: "Block reads + cache hits per second.",
  },
  slow_queries: {
    title: "Slow queries",
    description: "Rate of queries exceeding 1s.",
  },
  replica_lag: {
    title: "Replica lag",
    description: "Highest replica lag across this cluster's replicas.",
  },
  // Object store
  bucket_size: {
    title: "Bucket size",
    description: "Total bytes stored in this bucket.",
  },
  request_count: {
    title: "Requests",
    description: "Total requests per second to the bucket.",
  },
  errors_4xx: {
    title: "4xx errors",
    description: "Client errors per second.",
  },
  errors_5xx: {
    title: "5xx errors",
    description: "Server errors per second.",
  },
  egress_bytes: {
    title: "Egress",
    description: "Bytes downloaded per second from this bucket.",
  },
  // Hosted model (vLLM, #2064)
  tokens_per_second: {
    title: "Tokens/sec",
    description: "Generated tokens per second across replicas.",
  },
  requests_running: {
    title: "Running",
    description: "Requests being decoded right now.",
  },
  requests_waiting: {
    title: "Waiting",
    description: "Requests queued for a slot.",
  },
  ttft_p95: {
    title: "Time to first token",
    description: "95th percentile time to the first generated token.",
  },
  kv_cache_usage: {
    title: "KV cache",
    description: "Share of the GPU KV cache in use.",
  },
};

const KIND_HEADER: Record<string, { label: string; description: string }> = {
  postgres: {
    label: "Database",
    description: "Connections, IO, slow-query rate, and replica lag for this database.",
  },
  object_store: {
    label: "Storage",
    description: "Bucket size, request rate, error breakdown, and egress for this bucket.",
  },
  model_endpoint: {
    label: "Model",
    description: "Throughput, queue, time to first token and KV cache for this hosted model.",
  },
};

interface PanelProps {
  managedServiceId: string;
  rangeSeconds?: number;
}

/**
 * Per-binding metrics panel — wraps one ManagedService row. Returns
 * null when the resolver returns null (unsupported kind / missing
 * service) so the caller doesn't need to know which kinds we support
 * at the GraphQL layer.
 */
export function ManagedServiceMetricsPanel({ managedServiceId, rangeSeconds }: PanelProps) {
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  const rangeS = rangeSeconds ?? TIME_RANGE_OPTIONS.find((o) => o.key === range)?.seconds ?? 3600;

  const q = useQuery<MetricsResp>(GET_MANAGED_SERVICE_METRICS, {
    variables: { managedServiceId, rangeSeconds: rangeS },
    fetchPolicy: "cache-and-network",
  });

  const data = q.data?.astroliftAppManagedServiceMetrics ?? null;
  const isLoading = q.loading && !q.data;

  // Resolver returned null → unsupported kind. Skip render entirely
  // rather than blank out the page; the caller usually iterates an
  // app's full ManagedService list and renders only the ones we
  // support today.
  if (!isLoading && data === null) {
    return null;
  }

  const KindIcon =
    data?.kind === "postgres"
      ? DatabaseIcon
      : data?.kind === "model_endpoint"
        ? BrainCircuitIcon
        : HardDriveIcon;
  const header = KIND_HEADER[data?.kind ?? ""] ?? KIND_HEADER.object_store;
  const kindLabel = header.label;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <KindIcon className="size-4" />
              {kindLabel}
              <span className="text-muted-foreground font-mono text-xs">{data?.name ?? "—"}</span>
            </CardTitle>
            <CardDescription>{header.description}</CardDescription>
          </div>
          <RangePicker value={range} onChange={setRange} />
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-40 w-full" />
            ))}
          </div>
        ) : data && data.series.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.series.map((s) => (
              <MetricSeriesCard key={s.name} series={s} />
            ))}
          </div>
        ) : (
          <NoMetricsCallout />
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Iterator helper — render one panel per supported managed-service
 * binding on the app. Caller passes the full list; we filter by kind
 * here so the call site stays declarative.
 */
export interface ManagedServiceBindingLite {
  id: string;
  kind: string;
}

export function ManagedServiceMetricsList({
  managedServices,
}: {
  managedServices: ManagedServiceBindingLite[];
}) {
  const supported = managedServices.filter((m) => SUPPORTED_KINDS.has(m.kind));
  if (supported.length === 0) return null;
  return (
    <div className="space-y-4">
      {supported.map((m) => (
        <ManagedServiceMetricsPanel key={m.id} managedServiceId={m.id} />
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------
// One metric card
// ----------------------------------------------------------------------

function MetricSeriesCard({ series }: { series: AstroliftManagedServiceMetricSeries }) {
  const copy = METRIC_COPY[series.name] ?? {
    title: series.name,
    description: "",
  };
  const samples = series.samples;
  const hasData = samples.length > 0;
  const rows = React.useMemo(() => toRows(samples), [samples]);
  const formatter = pickFormatter(series.unit);

  return (
    <div className="bg-muted/10 rounded-md border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ChartSplineIcon className="text-muted-foreground size-3.5" /> {copy.title}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">{copy.description}</p>
      <div className="mt-3 h-28">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="ts"
                tick={{ fontSize: 10 }}
                tickFormatter={formatTickTs}
                minTickGap={32}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                width={56}
                tickFormatter={(v: number) => formatter(v)}
              />
              <Tooltip
                formatter={(v: number) => formatter(v)}
                labelFormatter={(label: string) => label}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="var(--chart-2)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-muted-foreground flex h-full items-center justify-center text-xs">
            No samples in this window.
          </p>
        )}
      </div>
      <p className="text-muted-foreground text-2xs mt-2">source: {series.source}</p>
    </div>
  );
}

interface ChartRow {
  ts: string;
  value: number;
}

function toRows(samples: AstroliftTimeSeriesPoint[]): ChartRow[] {
  return samples.map((s) => ({
    ts: new Date(s.ts).toLocaleTimeString(),
    value: s.value,
  }));
}

function formatTickTs(ts: string): string {
  const parts = ts.split(":");
  if (parts.length >= 2) {
    return `${parts[0]}:${parts[1]}`;
  }
  return ts;
}

function pickFormatter(unit: string): (v: number) => string {
  switch (unit) {
    case "bytes":
      return (v: number) => formatBytes(v);
    case "count":
      return (v: number) => v.toFixed(0);
    case "rps":
      return (v: number) => `${v.toFixed(2)}/s`;
    case "ratio":
      return (v: number) => `${(v * 100).toFixed(0)}%`;
    case "seconds":
      return (v: number) => (v < 1 ? `${(v * 1000).toFixed(0)} ms` : `${v.toFixed(2)} s`);
    default:
      return (v: number) => v.toFixed(2);
  }
}

function formatBytes(v: number): string {
  if (v <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const i = Math.min(Math.floor(Math.log2(v) / 10), units.length - 1);
  return `${(v / 2 ** (i * 10)).toFixed(2)} ${units[i]}`;
}

// ----------------------------------------------------------------------
// Range picker (mirrors GoldenSignalsPanel)
// ----------------------------------------------------------------------

function RangePicker({
  value,
  onChange,
}: {
  value: TimeRangeKey;
  onChange: (k: TimeRangeKey) => void;
}) {
  return (
    <div className="bg-muted/40 inline-flex rounded-md border p-0.5">
      {TIME_RANGE_OPTIONS.map((opt) => (
        <button
          key={opt.key}
          type="button"
          aria-pressed={value === opt.key}
          onClick={() => onChange(opt.key)}
          className={`h-7 rounded px-3 text-xs ${
            value === opt.key ? "bg-background shadow" : "text-muted-foreground"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------
// Empty state
// ----------------------------------------------------------------------

function NoMetricsCallout() {
  return (
    <p className="text-muted-foreground py-8 text-center text-xs">
      Metrics not yet flowing — either the cluster&apos;s Prometheus endpoint isn&apos;t configured,
      or the exporter sidecar hasn&apos;t emitted samples for this service yet.
    </p>
  );
}
