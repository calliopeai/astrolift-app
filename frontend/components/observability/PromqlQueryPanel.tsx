"use client";

import { AlertTriangleIcon, ChevronDownIcon, ChevronRightIcon, SearchCodeIcon } from "lucide-react";
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

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { usePromql } from "./use-promql";
import type {
  AstroliftExecutePromqlResult,
  AstroliftPromqlSeries,
  AstroliftTimeSeriesPoint,
} from "@/graphql/__generated__/schema";

/** What the app exposes (#1226): its own metric names. */
export interface MetricDiscovery {
  ok: boolean;
  error?: string | null;
  names: string[];
  truncated: boolean;
  limit: number;
}

export type PromqlQueryPanelProps = ReturnType<typeof usePromql>;

export const RANGE_OPTIONS = [
  { key: "15m", label: "15m", seconds: 15 * 60, step: 15 },
  { key: "1h", label: "1h", seconds: 60 * 60, step: 60 },
  { key: "6h", label: "6h", seconds: 6 * 60 * 60, step: 300 },
  { key: "24h", label: "24h", seconds: 24 * 60 * 60, step: 1800 },
] as const;
export type PromqlRangeKey = (typeof RANGE_OPTIONS)[number]["key"];
type RangeKey = PromqlRangeKey;
const DEFAULT_RANGE: RangeKey = "1h";

// Chart series colors mirror the GoldenSignalsPanel palette so the
// PromQL panel renders into the same visual language.
const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

interface MetricNamePickerProps {
  discovery: MetricDiscovery | null;
  loading: boolean;
  onPick: (metric: string) => void;
}

/**
 * The app's own metric names, as clickable chips (#1226).
 *
 * The Query box could always run PromQL; what an operator lacked was any
 * way to know what their app exposes. Everything the platform emits into
 * the same namespace — cAdvisor, kube-state-metrics, the ingress
 * controller — is filtered server-side, because those have their own
 * panels and would bury the app's handful of business metrics.
 */
function MetricNamePicker({ discovery, loading, onPick }: MetricNamePickerProps) {
  const [filter, setFilter] = React.useState("");

  if (loading) {
    return <p className="text-muted-foreground text-xs">Looking up what this app exposes…</p>;
  }
  if (discovery === null) return null;

  if (!discovery.ok) {
    // Not an error state for the panel: free-form PromQL still works
    // without discovery, so this says what is missing and gets out of
    // the way.
    return (
      <p className="text-muted-foreground text-xs">
        Can&apos;t list this app&apos;s metrics ({discovery.error ?? "unknown reason"}). You can
        still run a query below.
      </p>
    );
  }

  if (discovery.names.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        This app exposes no metrics of its own yet. Add{" "}
        <code className="font-mono">[workloads.&lt;name&gt;.metrics]</code> with a{" "}
        <code className="font-mono">port</code> to your astrolift.toml and redeploy.
      </p>
    );
  }

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? discovery.names.filter((n) => n.toLowerCase().includes(needle))
    : discovery.names;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Label className="text-muted-foreground text-xs">This app exposes</Label>
        <Input
          aria-label="Filter metrics"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter metrics…"
          className="h-7 max-w-56 text-xs"
        />
        {discovery.truncated && (
          <span className="text-muted-foreground text-2xs">
            showing the first {discovery.limit} — this app emits more distinct metric names than
            that, which usually means a label belongs in a label rather than in the name
          </span>
        )}
      </div>
      <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
        {shown.map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => onPick(name)}
            className="border-border hover:bg-accent text-2xs rounded border px-2 py-0.5 font-mono"
          >
            {name}
          </button>
        ))}
        {shown.length === 0 && (
          <span className="text-muted-foreground text-xs">
            No metric matches &ldquo;{filter}&rdquo;.
          </span>
        )}
      </div>
    </div>
  );
}

/** Pure (Storybook first): the query runs and the names load through usePromql. */
export function PromqlQueryPanel({
  open,
  onOpenChange: setOpen,
  discovery,
  discoveryLoading,
  running: loading,
  transportError,
  result,
  onRun: run,
}: PromqlQueryPanelProps) {
  const [query, setQuery] = React.useState("");
  const [range, setRange] = React.useState<RangeKey>(DEFAULT_RANGE);

  // Clicking a name writes the simplest expression that charts it rather
  // than the bare name: a counter graphed raw is a monotonic ramp, which
  // tells an operator nothing. They can edit it — the box is still a box.
  const onPickMetric = (metric: string) => {
    const expr = metric.endsWith("_total") ? `sum(rate(${metric}[5m]))` : `sum(${metric})`;
    setQuery(expr);
  };

  const onRun = () => {
    if (!query.trim()) return;
    run(query.trim(), range);
  };

  const Chevron = open ? ChevronDownIcon : ChevronRightIcon;

  return (
    <Card>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="hover:text-foreground -m-1 flex w-full items-center gap-2 rounded p-1 text-left"
              aria-expanded={open}
            >
              <Chevron className="text-muted-foreground size-4" />
              <CardTitle className="flex flex-1 items-center gap-2 text-base">
                <SearchCodeIcon className="size-4" /> Query
              </CardTitle>
            </button>
          </CollapsibleTrigger>
          <CardDescription className="ml-6">
            Run an ad-hoc PromQL expression against the cluster&apos;s Prometheus stack.
          </CardDescription>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-4">
            <MetricNamePicker
              discovery={discovery}
              loading={discoveryLoading}
              onPick={onPickMetric}
            />

            <div className="space-y-2">
              <Textarea
                rows={2}
                // A textarea whose only description is a placeholder has no
                // accessible name, and the picker's filter box above it is
                // also a textbox — so a screen reader had two unlabelled
                // fields to tell apart.
                aria-label="PromQL query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder='sum by (route) (rate(http_requests_total{job="myapp"}[5m]))'
                className="font-mono text-xs"
              />
              <div className="flex flex-wrap items-center gap-2">
                <Select value={range} onValueChange={(v) => setRange(v as RangeKey)}>
                  <SelectTrigger size="sm" className="h-8 w-24">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RANGE_OPTIONS.map((o) => (
                      <SelectItem key={o.key} value={o.key}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  onClick={onRun}
                  disabled={loading || !query.trim()}
                  className="h-8"
                >
                  {loading ? "Running…" : "Run"}
                </Button>
              </div>
            </div>

            <PromqlBody loading={loading} transportError={transportError} result={result} />
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}

interface PromqlBodyProps {
  loading: boolean;
  transportError: string | null;
  result: AstroliftExecutePromqlResult | null;
}

function PromqlBody({ loading, transportError, result }: PromqlBodyProps) {
  if (loading) {
    return <Skeleton className="h-56 w-full" />;
  }
  if (transportError) {
    return <ErrorCallout message={transportError} />;
  }
  if (!result) {
    return (
      <p className="text-muted-foreground py-8 text-center text-sm">
        Enter a PromQL expression above
      </p>
    );
  }
  if (!result.ok) {
    return <ErrorCallout message={result.error || "PromQL execution failed"} />;
  }
  if (result.series.length === 0) {
    return (
      <p className="text-muted-foreground py-8 text-center text-sm">Query returned no series.</p>
    );
  }
  return <PromqlChart series={result.series} />;
}

function PromqlChart({ series }: { series: AstroliftPromqlSeries[] }) {
  const { rows, keys } = React.useMemo(() => mergeSeries(series), [series]);
  return (
    <div className="space-y-3">
      <div className="h-56">
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
              tickFormatter={(v: number) => formatValue(v)}
              width={64}
            />
            <Tooltip
              formatter={(v: number, name: string) => [formatValue(v), name]}
              labelFormatter={(label: string) => label}
            />
            {keys.map((k, idx) => (
              <Line
                key={k}
                type="monotone"
                dataKey={k}
                stroke={SERIES_COLORS[idx % SERIES_COLORS.length]}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ul className="space-y-1 text-xs">
        {series.map((s, idx) => (
          <li key={seriesKey(s, idx)} className="flex items-start gap-2 font-mono">
            <span
              aria-hidden
              className="mt-1 inline-block size-2 shrink-0 rounded-full"
              style={{ background: SERIES_COLORS[idx % SERIES_COLORS.length] }}
            />
            <span className="text-muted-foreground break-all">{labelString(s.metricLabels)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ErrorCallout({ message }: { message: string }) {
  return (
    <div className="border-destructive/40 bg-destructive/5 text-destructive flex items-start gap-2 rounded-md border p-3 text-sm">
      <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-medium">PromQL error</p>
        <pre className="mt-1 font-mono text-xs whitespace-pre-wrap">{message}</pre>
      </div>
    </div>
  );
}

type ChartRow = Record<string, number | string | null>;

interface MergedSeries {
  rows: ChartRow[];
  keys: string[];
}

function mergeSeries(series: AstroliftPromqlSeries[]): MergedSeries {
  const tsIndex = new Map<string, ChartRow>();
  const keys: string[] = [];
  series.forEach((s, idx) => {
    const k = seriesKey(s, idx);
    keys.push(k);
    for (const point of s.values) {
      const tsKey = String(point.ts);
      let row = tsIndex.get(tsKey);
      if (!row) {
        row = { ts: tickFromPoint(point), _key: tsKey };
        tsIndex.set(tsKey, row);
      }
      row[k] = point.value;
    }
  });
  const rows = Array.from(tsIndex.values()).sort((a, b) =>
    String(a._key).localeCompare(String(b._key))
  );
  return { rows, keys };
}

function seriesKey(s: AstroliftPromqlSeries, idx: number): string {
  // The metricLabels JSON scalar is typed as `unknown` at the schema
  // level; coerce to a flat string map for legend rendering and use the
  // serialized form as the recharts dataKey so duplicate label-sets
  // (which Prometheus already de-dupes server-side) can't collide.
  const labels = asLabelMap(s.metricLabels);
  const ser = labelString(labels);
  return ser || `series_${idx}`;
}

function labelString(raw: unknown): string {
  const labels = asLabelMap(raw);
  const entries = Object.entries(labels)
    .filter(([k]) => k !== "__name__")
    .sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) {
    return typeof labels["__name__"] === "string" ? labels["__name__"] : "{}";
  }
  const name = typeof labels["__name__"] === "string" ? labels["__name__"] : "";
  const body = entries.map(([k, v]) => `${k}="${v}"`).join(", ");
  return name ? `${name}{${body}}` : `{${body}}`;
}

function asLabelMap(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    out[k] = String(v);
  }
  return out;
}

function tickFromPoint(point: AstroliftTimeSeriesPoint): string {
  const raw = point.ts;
  // The resolver emits a DateTime string; some Prometheus paths send a
  // unix-seconds string, hence the dual-decode.
  if (/^[0-9.]+$/.test(raw)) {
    const asNum = Number(raw);
    if (asNum > 0) return new Date(asNum * 1000).toLocaleTimeString();
  }
  const d = new Date(raw);
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString();
  }
  return raw;
}

function formatTickTs(ts: string): string {
  const parts = ts.split(":");
  if (parts.length >= 2) {
    return `${parts[0]}:${parts[1]}`;
  }
  return ts;
}

function formatValue(v: number): string {
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  if (v === 0) return "0";
  return v.toPrecision(3);
}
