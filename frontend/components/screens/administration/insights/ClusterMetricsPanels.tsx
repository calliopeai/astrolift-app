"use client";

import { BarChart3Icon, CloudIcon, RefreshCwIcon, WifiOffIcon } from "lucide-react";
import Link from "next/link";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import {
  TONE_COLORS,
  fmtTs,
  fmtValue,
  healthTone,
  seriesTone,
  utilizationTone,
  type RangePoint,
  type RangeSeries,
  type Tone,
} from "./metrics-format";
import type { usePrometheusPanel, useSystemMetricsPanel } from "./use-admin-metrics";

export type PrometheusPanelProps = ReturnType<typeof usePrometheusPanel>;
export type SystemMetricsPanelProps = ReturnType<typeof useSystemMetricsPanel>;

// ─── Prometheus panel: KPI tiles (instant) + sparkline grid (range) ───
export function PrometheusPanel({
  slug,
  instant,
  instantLoading,
  range,
  rangeLoading,
}: PrometheusPanelProps) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <BarChart3Icon className="size-4" /> Cluster saturation
        </h3>
        <p className="text-muted-foreground text-xs">
          In-cluster Prometheus golden signals — current snapshot and trend over the selected
          window.
        </p>
      </div>

      {/* KPI tiles from the instant query */}
      {instantLoading && !instant ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : !instant?.available ? (
        <MetricsUnavailableCard
          reason={instant?.reason ?? null}
          settingsHref={`/clusters/${slug}/settings`}
        />
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <KpiTile
            label="CPU utilization"
            value={instant.cpuUtilization}
            unit="ratio"
            tone={utilizationTone(instant.cpuUtilization)}
          />
          <KpiTile
            label="Memory utilization"
            value={instant.memoryUtilization}
            unit="ratio"
            tone={utilizationTone(instant.memoryUtilization)}
          />
          <KpiTile label="Nodes" value={instant.nodeCount} unit="count" tone="neutral" />
          <KpiTile
            label="Pod health"
            value={instant.podRunningRatio}
            unit="ratio"
            tone={healthTone(instant.podRunningRatio)}
          />
        </div>
      )}

      {/* Sparkline grid from the range query */}
      {rangeLoading && !range ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : !range?.available ? (
        // Instant panel already surfaced the unavailable reason above;
        // only show a second callout when the instant query happened to
        // be available but the range one wasn't (rare — different cache
        // entry), to avoid a duplicate card in the common case.
        instant?.available ? (
          <MetricsUnavailableCard
            reason={range?.reason ?? null}
            settingsHref={`/clusters/${slug}/settings`}
          />
        ) : null
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {range.series.map((s) => (
            <MetricAreaCard key={s.metric} series={s} />
          ))}
        </div>
      )}
    </section>
  );
}

// ─── System metrics panel (cloud provider · CloudWatch ALB) ───────────
export function SystemMetricsPanel({ providerSlug, metrics, loading }: SystemMetricsPanelProps) {
  const scopeNote =
    metrics?.available && metrics.appNamespace
      ? `${metrics.source === "cloudwatch" ? "CloudWatch ALB" : metrics.source} · namespace ${metrics.appNamespace}`
      : "Cloud-provider metrics — no in-app instrumentation required.";

  return (
    <section className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <CloudIcon className="size-4" /> System metrics
        </h3>
        <p className="text-muted-foreground text-xs">{scopeNote}</p>
      </div>

      {loading && !metrics ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : !metrics?.available ? (
        <SystemMetricsUnavailableCard
          reason={metrics?.reason ?? null}
          providerSlug={providerSlug}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {metrics.series.map((s) => (
            <MetricAreaCard key={s.metric} series={s} />
          ))}
        </div>
      )}
    </section>
  );
}

// ─── KPI tile ─────────────────────────────────────────────────────────
function KpiTile({
  label,
  value,
  unit,
  tone,
}: {
  label: string;
  value: number | null;
  unit: string;
  tone: Tone;
}) {
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">{label}</p>
      <p className={`text-2xl font-semibold tabular-nums ${TONE_COLORS[tone].text}`}>
        {fmtValue(value, unit)}
      </p>
    </div>
  );
}

// ─── Single metric area card (shared: Prometheus + system series) ─────
function MetricAreaCard({ series }: { series: RangeSeries }) {
  const tone = seriesTone(series);
  const colors = TONE_COLORS[tone];
  const chartData = series.points.map((p) => ({ ts: p.ts, value: p.value }));

  return (
    <Card className="overflow-hidden !rounded-none shadow-md">
      <CardHeader className="px-4 pt-4 pb-2">
        <span className="text-muted-foreground text-xs tracking-wide uppercase">
          {series.label}
        </span>
        <CardTitle className={`text-2xl font-semibold tabular-nums ${colors.text}`}>
          {fmtValue(series.current, series.unit)}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {chartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={chartData} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`sysgrad-${series.metric}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={colors.stroke} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={colors.stroke} stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="ts" hide type="number" domain={["dataMin", "dataMax"]} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const pt = payload[0].payload as RangePoint;
                  return (
                    <div className="bg-popover rounded border px-2 py-1 text-xs shadow-md">
                      <div className="text-muted-foreground font-mono">{fmtTs(pt.ts)}</div>
                      <div className={`font-semibold ${colors.text}`}>
                        {fmtValue(pt.value, series.unit)}
                      </div>
                    </div>
                  );
                }}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={colors.stroke}
                strokeWidth={2}
                fill={`url(#sysgrad-${series.metric})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[80px] items-center justify-center">
            <span className="text-muted-foreground text-xs">
              {series.points.length === 0 ? "No data in window" : "Collecting data…"}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Prometheus unavailable card ──────────────────────────────────────
function MetricsUnavailableCard({
  reason,
  settingsHref,
}: {
  reason: string | null;
  settingsHref: string;
}) {
  const isNoEndpoint = reason === "no_endpoint";
  const Icon = isNoEndpoint ? RefreshCwIcon : WifiOffIcon;
  const title = isNoEndpoint ? "No Prometheus endpoint" : "Prometheus unreachable";
  const body = isNoEndpoint
    ? "The control plane hasn't discovered a Prometheus endpoint for this cluster yet."
    : "The control plane can't reach the Prometheus endpoint stored for this cluster.";
  const hint = isNoEndpoint
    ? "Run Refresh cluster management from Settings to auto-discover the endpoint, or set prometheus_endpoint in provider_config."
    : "Verify the endpoint is reachable from the control plane on port 9090.";

  return (
    <div className="flex items-start gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
      <Icon className="mt-0.5 size-5 shrink-0 text-amber-500" />
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground text-sm">{body}</p>
        <p className="text-muted-foreground text-xs">{hint}</p>
        {isNoEndpoint && (
          <Link
            href={settingsHref}
            className="text-primary mt-2 inline-block text-xs underline-offset-4 hover:underline"
          >
            Go to cluster settings →
          </Link>
        )}
      </div>
    </div>
  );
}

// ─── System-metrics unavailable card ──────────────────────────────────
function SystemMetricsUnavailableCard({
  reason,
  providerSlug,
}: {
  reason: string | null;
  providerSlug: string;
}) {
  const notSupported = reason === "not_supported";
  const Icon = notSupported ? CloudIcon : WifiOffIcon;
  const title = notSupported
    ? "Cloud metrics not available for this provider"
    : "Cloud metrics unreachable";
  const body = notSupported
    ? `The ${providerSlug} driver has no cloud-metrics integration wired yet. System metrics are sourced from CloudWatch on AWS clusters today.`
    : "The cloud provider's metrics API couldn't be reached (missing credentials or throttled). In-cluster Prometheus metrics above are unaffected.";

  return (
    <div className="border-border/70 text-muted-foreground flex items-start gap-3 rounded-md border border-dashed p-3 text-sm">
      <Icon className="text-muted-foreground/70 mt-0.5 size-5 shrink-0" />
      <div className="space-y-1">
        <p className="text-foreground font-medium">{title}</p>
        <p>{body}</p>
      </div>
    </div>
  );
}
