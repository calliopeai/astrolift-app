"use client";

import { BarChart3Icon, CloudIcon, RefreshCwIcon, WifiOffIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useId } from "react";
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
  const t = useTranslations("administration.metrics");
  return (
    <section className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <BarChart3Icon className="size-4" /> {t("saturationTitle")}
        </h3>
        <p className="text-muted-foreground text-xs">{t("saturationDescription")}</p>
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
            label={t("cpu")}
            value={instant.cpuUtilization}
            unit="ratio"
            tone={utilizationTone(instant.cpuUtilization)}
          />
          <KpiTile
            label={t("memory")}
            value={instant.memoryUtilization}
            unit="ratio"
            tone={utilizationTone(instant.memoryUtilization)}
          />
          <KpiTile label={t("nodes")} value={instant.nodeCount} unit="count" tone="neutral" />
          <KpiTile
            label={t("pods")}
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
  const t = useTranslations("administration.metrics");
  const scopeNote =
    metrics?.available && metrics.appNamespace
      ? t("scope", {
          source: metrics.source === "cloudwatch" ? "CloudWatch ALB" : metrics.source,
          namespace: metrics.appNamespace,
        })
      : t("systemDescription");

  return (
    <section className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <CloudIcon className="size-4" /> {t("systemTitle")}
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
  const t = useTranslations("administration.metrics");
  const gradientId = `sysgrad-${useId()}`;
  const tone = seriesTone(series);
  const colors = TONE_COLORS[tone];
  const chartData = series.points.map((p) => ({ ts: p.ts, value: p.value }));

  return (
    <Card className="overflow-hidden !rounded-none shadow-md">
      <CardHeader className="px-4 pt-4 pb-2">
        <span className="text-muted-foreground text-xs tracking-wide uppercase">
          {t.has(`series.${series.metric}`) ? t(`series.${series.metric}`) : series.label}
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
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
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
                fill={`url(#${gradientId})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[80px] items-center justify-center">
            <span className="text-muted-foreground text-xs">
              {series.points.length === 0 ? t("emptyWindow") : t("collecting")}
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
  const t = useTranslations("administration.metrics");
  const isNoEndpoint = reason === "no_endpoint";
  const Icon = isNoEndpoint ? RefreshCwIcon : WifiOffIcon;
  const title = t(isNoEndpoint ? "promNoEndpointTitle" : "promUnreachableTitle");
  const body = t(isNoEndpoint ? "promNoEndpointBody" : "promUnreachableBody");
  const hint = t(isNoEndpoint ? "promNoEndpointHint" : "promUnreachableHint");

  return (
    <div className="border-warning-border bg-warning/5 flex items-start gap-3 rounded-md border p-3">
      <Icon className="text-warning mt-0.5 size-5 shrink-0" />
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground text-sm">{body}</p>
        <p className="text-muted-foreground text-xs">{hint}</p>
        {isNoEndpoint && (
          <Link
            href={settingsHref}
            className="text-primary mt-2 inline-block text-xs underline-offset-4 hover:underline"
          >
            {t("clusterSettings")}
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
  const t = useTranslations("administration.metrics");
  const notSupported = reason === "not_supported";
  const Icon = notSupported ? CloudIcon : WifiOffIcon;
  const title = t(notSupported ? "cloudUnsupportedTitle" : "cloudUnreachableTitle");
  const body = notSupported
    ? t("cloudUnsupportedBody", { provider: providerSlug })
    : t("cloudUnreachableBody");

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
