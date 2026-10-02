"use client";

/**
 * GoldenSignalsPanel — the SRE-grade slice of the App > Observability
 * tab (#380).
 *
 * Surfaces the four golden signals (traffic, errors, latency,
 * saturation) plus a per-HTTP-status-code stacked breakdown, all
 * wired to the cluster-side Prometheus stack via the
 * ``astroliftAppGoldenSignals`` + ``astroliftAppStatusCodeBreakdown``
 * resolvers.
 *
 * Each signal displays the server's effective scope and provenance.
 * Missing or failed measurements stay unavailable, while a healthy
 * sibling remains visible. Legacy envelopes without provenance keep
 * their structural empty state and never imply a workload measurement.
 */

import { AlertTriangleIcon, ChartSplineIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/EmptyState";
import {
  type ObservabilityPanelReason,
  type PanelEmptyMessage,
  panelEmptyState,
} from "@/components/observability/panel-reason";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftAppGoldenSignal,
  AstroliftStatusCodeSeries,
  AstroliftTimeSeriesPoint,
  GoldenSignalKind,
} from "@/graphql/__generated__/schema";

import type { StatusBreakdownWithReason, useGoldenSignals } from "./use-golden-signals";
import { SignalMeasurement } from "./SignalMeasurement";
import {
  PROMETHEUS_DOC_LINK,
  STATUS_CLASS_COLORS,
  STATUS_CLASS_ORDER,
  TIME_RANGE_OPTIONS,
  TimeRangeKey,
  formatSignalValue,
} from "./golden-signals-types";

export type GoldenSignalsPanelProps = ReturnType<typeof useGoldenSignals>;

export function GoldenSignalsPanel({
  range,
  onRangeChange: setRange,
  signals,
  reason: signalsReason,
  loading: isLoading,
  onRetry,
  statusBreakdown,
  statusLoading,
  onStatusRetry,
}: GoldenSignalsPanelProps) {
  // Pick out the individual signals from the backend's flat list so
  // the rest of the render is a simple grid of cards. Latency rolls up
  // p50 + p90 + p99 into one card with a stacked line.
  const signalsByKind = React.useMemo(() => {
    const out: Partial<Record<GoldenSignalKind, AstroliftAppGoldenSignal>> = {};
    for (const s of signals ?? []) {
      out[s.name] = s;
    }
    return out;
  }, [signals]);

  // When the panel can't produce metrics for a structural reason (no
  // Prometheus endpoint, or a load error), render ONE honest message
  // instead of a wall of empty cards. OK / NO_DATA_YET fall through to
  // the card grid — the per-card "no data in window" is the right
  // granularity there.
  const showPanelEmpty =
    !isLoading &&
    !signals?.some((signal) => signal.measurement) &&
    (signalsReason === "NOT_CONFIGURED" ||
      signalsReason === "NOT_SUPPORTED_BY_PROVIDER" ||
      signalsReason === "ERROR");
  const panelEmpty = panelEmptyState(signalsReason ?? "NO_DATA_YET", {
    thing: "metrics",
    notConfigured: "Prometheus endpoint isn't wired for this cluster.",
    provider: "this cloud",
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-medium">Golden signals</h3>
          <p className="text-muted-foreground text-xs">
            Traffic, errors, latency, and saturation pulled live from the cluster&apos;s Prometheus
            stack.
          </p>
        </div>
        <TimeRangePicker value={range} onChange={setRange} />
      </div>

      {showPanelEmpty ? (
        <NoMetricsCallout
          title={panelEmpty.title}
          description={panelEmpty.description}
          onRetry={signalsReason === "ERROR" ? onRetry : undefined}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <SignalCard
              title="Traffic"
              description="Requests per second (sum across all replicas)."
              signal={signalsByKind.TRAFFIC}
              loading={isLoading}
              lineColor="var(--chart-2)"
            />
            <SignalCard
              title="Errors"
              description="5xx rate divided by total request rate."
              signal={signalsByKind.ERRORS}
              loading={isLoading}
              lineColor="var(--chart-1)"
            />
            {/* Latency #640 — renders p50 / p95 / p99 by default; p90 stays
                on the wire for legacy consumers but moves to the PromQL
                disclosure to keep the chart legible. */}
            <LatencyCard
              p50={signalsByKind.LATENCY_P50}
              p95={signalsByKind.LATENCY_P95}
              p99={signalsByKind.LATENCY_P99}
              p90={signalsByKind.LATENCY_P90}
              loading={isLoading}
            />
            <SignalCard
              title="Saturation (CPU)"
              description="Container CPU usage divided by CPU limits."
              signal={signalsByKind.SATURATION_CPU}
              loading={isLoading}
              lineColor="var(--chart-4)"
            />
            {/* #642 — memory saturation completes the saturation pair.
                Working-set bytes vs the memory limit; > 100% means an OOM
                kill is imminent. */}
            <SignalCard
              title="Saturation (memory)"
              description="Working-set memory divided by memory limits."
              signal={signalsByKind.SATURATION_MEMORY}
              loading={isLoading}
              lineColor="var(--chart-5)"
            />
            {/* Sixth grid cell — fills the slot beside Saturation (memory)
                instead of a lonely full-width row below. */}
            <StatusCodeCard
              breakdown={statusBreakdown}
              loading={statusLoading}
              onRetry={onStatusRetry}
            />
          </div>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Time-range picker
// ----------------------------------------------------------------------

interface TimeRangePickerProps {
  value: TimeRangeKey;
  onChange: (k: TimeRangeKey) => void;
}

function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  return (
    <div className="bg-muted/40 inline-flex rounded-md border p-0.5">
      {TIME_RANGE_OPTIONS.map((opt) => (
        <Button
          key={opt.key}
          size="sm"
          variant={opt.key === value ? "default" : "ghost"}
          className="h-7 px-3 text-xs"
          onClick={() => onChange(opt.key)}
        >
          {opt.label}
        </Button>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------
// Single-signal card
// ----------------------------------------------------------------------

interface SignalCardProps {
  title: string;
  description: string;
  signal: AstroliftAppGoldenSignal | undefined;
  loading: boolean;
  lineColor: string;
}

// A request-path signal with no source at all reads exactly like a
// healthy app with no traffic unless we say otherwise (#1708). The
// backend labels each signal; this turns that label into the card's
// empty state.
const RED_NOT_CONFIGURED =
  "Nothing feeds this signal: the app exposes no Prometheus metrics (set metrics.enabled on a workload) and this cluster's ingress controller has no metrics mapping.";

function signalEmptyState(
  signal: AstroliftAppGoldenSignal | undefined,
  thing: string
): PanelEmptyMessage {
  return panelEmptyState(signal?.reason ?? "NO_DATA_YET", {
    thing,
    notConfigured: RED_NOT_CONFIGURED,
    provider: "this cloud",
  });
}

function SignalCard({ title, description, signal, loading, lineColor }: SignalCardProps) {
  const samples = signal?.samples ?? [];
  const unit = signal?.unit ?? "";
  const promql = signal?.promql ?? "";
  const hasData = samples.length > 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <ChartSplineIcon className="size-4" /> {title}
        </CardTitle>
        <CardDescription className="text-xs">{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {!loading && <SignalMeasurement signal={signal} />}
        {loading ? (
          <Skeleton className="h-40 w-full" />
        ) : !signal || !hasData ? (
          <NoMetricsCallout
            title={signalEmptyState(signal, title.toLowerCase()).title}
            description={signalEmptyState(signal, title.toLowerCase()).description}
          />
        ) : (
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={toChartRows(samples, unit)}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="ts"
                  tick={{ fontSize: 10 }}
                  tickFormatter={formatTickTs}
                  minTickGap={32}
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: number) => formatSignalValue(v, unit)}
                  width={60}
                />
                <Tooltip
                  formatter={(v: number) => formatSignalValue(v, unit)}
                  labelFormatter={(label: string) => label}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={lineColor}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <PromQLDisclosure promql={promql} />
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------
// Latency card — three quantile lines stacked
// ----------------------------------------------------------------------

interface LatencyCardProps {
  p50: AstroliftAppGoldenSignal | undefined;
  p95: AstroliftAppGoldenSignal | undefined;
  p99: AstroliftAppGoldenSignal | undefined;
  /** Wire-only — p90 is rendered in the PromQL disclosure but not the
   *  chart so the three default lines (p50 / p95 / p99) stay readable
   *  (#640). */
  p90: AstroliftAppGoldenSignal | undefined;
  loading: boolean;
}

function LatencyCard({ p50, p95, p99, p90, loading }: LatencyCardProps) {
  const unit = p50?.unit ?? p95?.unit ?? p99?.unit ?? "seconds";
  const rows = React.useMemo(() => mergeLatencyRows(p50, p95, p99), [p50, p95, p99]);
  // p90 is intentionally exposed via the disclosure only — the chart
  // renders three lines so the SLO-typical p95 isn't visually crowded
  // out by a fourth nearby quantile (#640).
  const promql = [p50?.promql, p95?.promql, p99?.promql, p90?.promql].filter(Boolean).join("\n\n");
  const hasData = rows.length > 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <ChartSplineIcon className="size-4" /> Latency
        </CardTitle>
        <CardDescription className="text-xs">
          p50, p95, p99 request latency. p90 available via the PromQL disclosure.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!loading &&
          [p50, p95, p99, p90].filter(Boolean).map((signal) => (
            <div key={signal!.name}>
              <p className="text-xs font-medium">
                {signal!.name.replace("LATENCY_", "").toLowerCase()}
              </p>
              <SignalMeasurement signal={signal} />
            </div>
          ))}
        {loading ? (
          <Skeleton className="h-40 w-full" />
        ) : !hasData ? (
          <NoMetricsCallout
            title={signalEmptyState(p95 ?? p50 ?? p99, "latency").title}
            description={signalEmptyState(p95 ?? p50 ?? p99, "latency").description}
          />
        ) : (
          <div className="h-40">
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
                  tickFormatter={(v: number) => formatSignalValue(v, unit)}
                  width={60}
                />
                <Tooltip
                  formatter={(v: number) => formatSignalValue(v, unit)}
                  labelFormatter={(label: string) => label}
                />
                <Line
                  type="monotone"
                  dataKey="p50"
                  stroke="var(--chart-2)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="p95"
                  stroke="var(--chart-3)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="p99"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <PromQLDisclosure promql={promql} />
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------
// Status-code stacked card
// ----------------------------------------------------------------------

interface StatusCodeCardProps {
  breakdown: StatusBreakdownWithReason | null;
  loading: boolean;
  onRetry?: () => void;
}

const EMPTY_STATUS_SERIES: AstroliftStatusCodeSeries[] = [];

function StatusCodeCard({ breakdown, loading, onRetry }: StatusCodeCardProps) {
  const series = breakdown?.series ?? EMPTY_STATUS_SERIES;
  const promql = breakdown?.promql ?? "";
  // `null` is reserved for "no such app"; a populated breakdown carries
  // a reason (#1111). Fall back to NOT_CONFIGURED when the whole object
  // is absent so the copy stays honest rather than hedged.
  const reason: ObservabilityPanelReason = breakdown?.reason ?? "NOT_CONFIGURED";
  const empty = panelEmptyState(reason, {
    thing: "traffic",
    notConfigured: "Prometheus endpoint isn't wired for this cluster.",
    provider: "this cloud",
  });
  // Pre-sort series into the canonical 2xx/3xx/4xx/5xx/other order so
  // the stacked-area legend reads top-to-bottom by severity.
  const orderedSeries = React.useMemo(() => {
    return [...series].sort(
      (a, b) =>
        STATUS_CLASS_ORDER.indexOf(a.codeClass as (typeof STATUS_CLASS_ORDER)[number]) -
        STATUS_CLASS_ORDER.indexOf(b.codeClass as (typeof STATUS_CLASS_ORDER)[number])
    );
  }, [series]);
  const rows = React.useMemo(() => mergeStatusRows(orderedSeries), [orderedSeries]);
  const topByClass = React.useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const s of orderedSeries) {
      map[s.codeClass] = s.topCodes ?? [];
    }
    return map;
  }, [orderedSeries]);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <ChartSplineIcon className="size-4" /> Status-code breakdown
        </CardTitle>
        <CardDescription className="text-xs">
          Stacked request rate by HTTP status class. Hover for the top individual codes in each
          class.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-56 w-full" />
        ) : !breakdown || rows.length === 0 ? (
          <NoMetricsCallout
            title={empty.title}
            description={empty.description}
            onRetry={reason === "ERROR" ? onRetry : undefined}
          />
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={rows} stackOffset="none">
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="ts"
                  tick={{ fontSize: 10 }}
                  tickFormatter={formatTickTs}
                  minTickGap={32}
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: number) => `${v.toFixed(1)}`}
                  width={48}
                />
                <Tooltip
                  formatter={(v: number, name: string) => {
                    const top = topByClass[name] ?? [];
                    const topSuffix = top.length > 0 ? ` (top: ${top.slice(0, 5).join(", ")})` : "";
                    return [`${v.toFixed(2)} req/s${topSuffix}`, name];
                  }}
                />
                {orderedSeries.map((s) => (
                  <Area
                    key={s.codeClass}
                    type="monotone"
                    dataKey={s.codeClass}
                    stackId="1"
                    stroke={STATUS_CLASS_COLORS[s.codeClass] ?? "var(--chart-5)"}
                    fill={STATUS_CLASS_COLORS[s.codeClass] ?? "var(--chart-5)"}
                    fillOpacity={0.55}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
        <PromQLDisclosure promql={promql} />
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

interface ChartRow {
  ts: string;
  value: number;
}

function toChartRows(samples: AstroliftTimeSeriesPoint[], _unit: string): ChartRow[] {
  return samples.map((s) => ({
    ts: new Date(s.ts).toLocaleTimeString(),
    value: s.value,
  }));
}

interface LatencyRow {
  ts: string;
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

function mergeLatencyRows(
  p50: AstroliftAppGoldenSignal | undefined,
  p95: AstroliftAppGoldenSignal | undefined,
  p99: AstroliftAppGoldenSignal | undefined
): LatencyRow[] {
  // The backend hits Prometheus with the same start/end/step for all
  // three quantile queries, so the per-quantile sample arrays line up
  // by index. Take the longest of the three as the baseline.
  const arrays = [p50?.samples, p95?.samples, p99?.samples];
  const longest = arrays.reduce<AstroliftTimeSeriesPoint[]>(
    (acc, arr) => ((arr?.length ?? 0) > acc.length ? arr! : acc),
    [] as AstroliftTimeSeriesPoint[]
  );
  return longest.map((point, i) => ({
    ts: new Date(point.ts).toLocaleTimeString(),
    p50: p50?.samples[i]?.value ?? null,
    p95: p95?.samples[i]?.value ?? null,
    p99: p99?.samples[i]?.value ?? null,
  }));
}

type StatusRow = Record<string, number | string>;

function mergeStatusRows(series: AstroliftStatusCodeSeries[]): StatusRow[] {
  // Pivot per-class series into a single row-per-timestamp shape that
  // recharts' stacked AreaChart can consume directly.
  const tsIndex = new Map<string, StatusRow>();
  for (const s of series) {
    for (const point of s.samples) {
      const tsKey = new Date(point.ts).toISOString();
      let row = tsIndex.get(tsKey);
      if (!row) {
        row = { ts: new Date(point.ts).toLocaleTimeString(), _key: tsKey };
        tsIndex.set(tsKey, row);
      }
      row[s.codeClass] = point.value;
    }
  }
  return Array.from(tsIndex.values()).sort((a, b) => String(a._key).localeCompare(String(b._key)));
}

function formatTickTs(ts: string): string {
  // ``ts`` is already a locale-time string; truncate to HH:MM for tick
  // density — full HH:MM:SS is too wide on a 7d range.
  const parts = ts.split(":");
  if (parts.length >= 2) {
    return `${parts[0]}:${parts[1]}`;
  }
  return ts;
}

// ----------------------------------------------------------------------
// PromQL disclosure
// ----------------------------------------------------------------------

interface PromQLDisclosureProps {
  promql: string;
}

function PromQLDisclosure({ promql }: PromQLDisclosureProps) {
  if (!promql) return null;
  return (
    <details className="mt-3">
      <summary className="text-muted-foreground cursor-pointer text-xs select-none">
        Show PromQL
      </summary>
      <pre className="bg-muted/40 text-2xs mt-2 overflow-x-auto rounded border p-2 font-mono whitespace-pre-wrap">
        {promql}
      </pre>
    </details>
  );
}

// ----------------------------------------------------------------------
// Empty-state callout
// ----------------------------------------------------------------------

function NoMetricsCallout({
  title = "No data in this window",
  description = "No data points landed inside the selected time range.",
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 text-center">
      <EmptyState
        icon={<AlertTriangleIcon className="size-5" />}
        title={title}
        description={description}
        secondary={
          onRetry ? (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          ) : undefined
        }
      />
      <Link href={PROMETHEUS_DOC_LINK} className="text-xs underline">
        See the docs
      </Link>
    </div>
  );
}
