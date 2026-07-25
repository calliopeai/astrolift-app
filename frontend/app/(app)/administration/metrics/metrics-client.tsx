"use client";

import { useState } from "react";

import Link from "next/link";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  BarChart3Icon,
  CloudIcon,
  RefreshCwIcon,
  ServerIcon,
  ServerOffIcon,
  WifiOffIcon,
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
  CLUSTER_SYSTEM_METRICS,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import {
  clusterOfflineMessage,
  heartbeatPresentation,
  isClusterLive,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";

// ─── Window config (drives every range query) ────────────────────────
// stepSeconds doubles as the CloudWatch period — all values are 60s
// multiples so they're valid CloudWatch periods as well as Prometheus
// steps.
const WINDOWS = [
  { label: "1h", rangeSeconds: 3_600, stepSeconds: 60 },
  { label: "6h", rangeSeconds: 21_600, stepSeconds: 300 },
  { label: "24h", rangeSeconds: 86_400, stepSeconds: 900 },
] as const;

type WindowLabel = (typeof WINDOWS)[number]["label"];

// ─── GraphQL response shapes (typed locally — the clusters surface uses
// plain gql documents + inline interfaces rather than codegen output,
// mirroring cluster-status-client). ─────────────────────────────────
interface Cluster {
  id: string;
  slug: string;
  name: string;
  providerPluginSlug: string;
  region: string;
  isActive: boolean;
  heartbeatStatus: HeartbeatStatus;
  heartbeatAgeSeconds: number | null;
}

interface ClustersResp {
  astroliftClusters: Cluster[];
}

interface RangePoint {
  ts: number;
  value: number;
}

interface RangeSeries {
  metric: string;
  label: string;
  unit: string;
  current: number | null;
  points: RangePoint[];
}

interface PromInstant {
  available: boolean;
  reason: string | null;
  nodeCount: number | null;
  podRunningRatio: number | null;
  cpuUtilization: number | null;
  memoryUtilization: number | null;
  deploymentReadyRatio: number | null;
}

interface PromInstantResp {
  astroliftClusterPrometheusMetrics: PromInstant;
}

interface PromRangeResp {
  astroliftClusterPrometheusRangeMetrics: {
    available: boolean;
    reason: string | null;
    rangeSeconds: number;
    stepSeconds: number;
    series: RangeSeries[];
  };
}

interface SystemMetricsResp {
  astroliftClusterSystemMetrics: {
    available: boolean;
    reason: string | null;
    source: string;
    appNamespace: string;
    rangeSeconds: number;
    stepSeconds: number;
    series: RangeSeries[];
  };
}

// ─── Value formatting + tone (shared across Prometheus + CloudWatch
// series; mirrors cluster-status-client so the dataviz reads
// identically across surfaces). ──────────────────────────────────────
function fmtValue(value: number | null, unit: string): string {
  if (value === null) return "—";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "count") return value < 0.1 ? "0" : value.toFixed(2);
  if (unit === "rps") return `${value.toFixed(value < 10 ? 2 : 0)} req/s`;
  if (unit === "seconds") {
    if (value < 0.001) return `${(value * 1_000_000).toFixed(0)}µs`;
    if (value < 1) return `${(value * 1000).toFixed(0)}ms`;
    return `${value.toFixed(2)}s`;
  }
  if (unit === "bytes_per_sec") {
    if (value < 1024) return `${value.toFixed(0)} B/s`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB/s`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB/s`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
  }
  return value.toFixed(2);
}

const TONE_COLORS = {
  ok: { text: "text-success-fg", stroke: "var(--success)", fill: "var(--success-bg)" },
  warn: { text: "text-warning-fg", stroke: "var(--warning)", fill: "var(--warning-bg)" },
  bad: { text: "text-destructive", stroke: "var(--danger)", fill: "var(--danger-bg)" },
  neutral: { text: "text-foreground", stroke: "var(--info)", fill: "var(--info-bg)" },
} as const;

type Tone = keyof typeof TONE_COLORS;

function utilizationTone(v: number | null): Tone {
  if (v === null) return "neutral";
  if (v < 0.7) return "ok";
  if (v < 0.9) return "warn";
  return "bad";
}

function healthTone(v: number | null): Tone {
  if (v === null) return "neutral";
  if (v >= 0.9) return "ok";
  if (v >= 0.7) return "warn";
  return "bad";
}

// Tone for a range/system series based on its metric + latest value.
// Utilization/latency: high is bad. Health ratios: high is good.
// Throughput/counts: neutral (volume isn't inherently good or bad).
function seriesTone(series: RangeSeries): Tone {
  const v = series.current;
  if (v === null) return "neutral";
  if (
    series.metric === "pod_running_ratio" ||
    series.metric === "deployment_ready_ratio"
  ) {
    return healthTone(v);
  }
  if (series.metric === "error_rate") {
    if (v < 0.01) return "ok";
    if (v < 0.05) return "warn";
    return "bad";
  }
  if (series.metric === "latency_p99" || series.metric === "latency_p95") {
    if (v < 0.1) return "ok";
    if (v < 0.5) return "warn";
    return "bad";
  }
  if (series.metric === "restart_rate") {
    if (v === 0) return "ok";
    if (v < 1) return "warn";
    return "bad";
  }
  if (series.metric === "network_rx" || series.metric === "request_rate") return "neutral";
  if (series.unit === "count") return "neutral";
  return utilizationTone(v);
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ─── Root ─────────────────────────────────────────────────────────────
export function AdminMetricsClient() {
  const [window, setWindow] = useState<WindowLabel>("1h");
  const win = WINDOWS.find((w) => w.label === window)!;

  const { data, loading } = useQuery<ClustersResp>(LIST_CLUSTERS, {
    pollInterval: 60_000,
  });
  const clusters = (data?.astroliftClusters ?? []).filter((c) => c.isActive);

  return (
    <PageShell
      title="Platform metrics"
      description="Fleet health, in-cluster Prometheus saturation, and cloud-provider system metrics across your organization's clusters."
      actions={<WindowSelector value={window} onChange={setWindow} />}
    >
      {loading && clusters.length === 0 ? (
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : clusters.length === 0 ? (
        <EmptyState
          icon={<ServerIcon className="size-5" />}
          title="No clusters registered"
          description="Register a tenant cluster to start collecting platform metrics. Cluster saturation and system metrics populate once a cluster is managed and reachable."
          actionHref="/clusters"
          actionLabel="Open clusters"
        />
      ) : (
        <div className="space-y-6">
          <FleetSummary clusters={clusters} />
          {clusters.map((c) => (
            <ClusterSection key={c.id} cluster={c} win={win} />
          ))}
        </div>
      )}
    </PageShell>
  );
}

// ─── Fleet summary strip ──────────────────────────────────────────────
function FleetSummary({ clusters }: { clusters: Cluster[] }) {
  const total = clusters.length;
  const connected = clusters.filter((c) => c.heartbeatStatus === "connected").length;
  const degraded = clusters.filter((c) => c.heartbeatStatus === "degraded").length;
  const offline = clusters.filter(
    (c) => c.heartbeatStatus === "offline" || c.heartbeatStatus === "never_seen",
  ).length;

  const tiles: { label: string; value: number; tone: Tone }[] = [
    { label: "Clusters", value: total, tone: "neutral" },
    { label: "Connected", value: connected, tone: connected > 0 ? "ok" : "neutral" },
    { label: "Degraded", value: degraded, tone: degraded > 0 ? "warn" : "neutral" },
    { label: "Offline", value: offline, tone: offline > 0 ? "bad" : "neutral" },
  ];

  return (
    <Card className="!rounded-none shadow-md">
      <CardContent className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="space-y-1">
            <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
              {t.label}
            </p>
            <p className={`text-2xl font-semibold tabular-nums ${TONE_COLORS[t.tone].text}`}>
              {t.value}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ─── Per-cluster section ──────────────────────────────────────────────
// Gates the driver/network-backed panels on the cheap heartbeat status
// already carried by LIST_CLUSTERS: when a cluster is offline / has no
// agent, we render a targeted placeholder INSTEAD of mounting the metric
// panels, so their Prometheus / CloudWatch queries never fire against an
// unreachable control-plane target (the "tabs spin forever" problem the
// keep-alive heartbeat was built to short-circuit).
function ClusterSection({ cluster, win }: { cluster: Cluster; win: (typeof WINDOWS)[number] }) {
  const live = isClusterLive(cluster.heartbeatStatus);
  const p = heartbeatPresentation(cluster.heartbeatStatus);

  return (
    <Card className="!rounded-none shadow-md">
      <CardHeader className="px-4 pt-4 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <ServerIcon className="text-muted-foreground size-4" />
          <CardTitle className="text-sm font-semibold">{cluster.name}</CardTitle>
          <span className="text-muted-foreground font-mono text-xs">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
          {cluster.region && (
            <span className="text-muted-foreground text-xs">{cluster.region}</span>
          )}
          <span
            className={`ml-auto inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${p.pill}`}
          >
            {live ? (
              <ActivityIcon className="size-3" />
            ) : cluster.heartbeatStatus === "never_seen" ? (
              <ServerOffIcon className="size-3" />
            ) : (
              <WifiOffIcon className="size-3" />
            )}
            {p.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-6 px-4 pb-4">
        {live ? (
          <>
            <PrometheusPanel clusterId={cluster.id} slug={cluster.slug} win={win} />
            <SystemMetricsPanel
              clusterId={cluster.id}
              providerSlug={cluster.providerPluginSlug}
              win={win}
            />
          </>
        ) : (
          <div className="border-border/70 text-muted-foreground flex items-start gap-3 rounded-md border border-dashed px-3 py-4 text-sm">
            <WifiOffIcon className="text-muted-foreground/70 mt-0.5 size-4 shrink-0" />
            <div className="space-y-1">
              <p>{clusterOfflineMessage(cluster.heartbeatStatus, cluster.heartbeatAgeSeconds)}</p>
              <Link
                href={`/clusters/${cluster.slug}/status`}
                className="text-primary inline-block text-xs underline-offset-4 hover:underline"
              >
                Open cluster status →
              </Link>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Prometheus panel: KPI tiles (instant) + sparkline grid (range) ───
function PrometheusPanel({
  clusterId,
  slug,
  win,
}: {
  clusterId: string;
  slug: string;
  win: (typeof WINDOWS)[number];
}) {
  const { data: instantData, loading: instantLoading } = useQuery<PromInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    { variables: { clusterId }, pollInterval: 60_000 },
  );
  const { data: rangeData, loading: rangeLoading } = useQuery<PromRangeResp>(
    CLUSTER_PROMETHEUS_RANGE_METRICS,
    {
      variables: {
        clusterId,
        rangeSeconds: win.rangeSeconds,
        stepSeconds: win.stepSeconds,
      },
      pollInterval: 60_000,
    },
  );

  const instant = instantData?.astroliftClusterPrometheusMetrics ?? null;
  const range = rangeData?.astroliftClusterPrometheusRangeMetrics ?? null;

  return (
    <section className="space-y-4">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <BarChart3Icon className="size-4" /> Cluster saturation
        </h3>
        <p className="text-muted-foreground text-xs">
          In-cluster Prometheus golden signals — current snapshot and trend over the selected window.
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
function SystemMetricsPanel({
  clusterId,
  providerSlug,
  win,
}: {
  clusterId: string;
  providerSlug: string;
  win: (typeof WINDOWS)[number];
}) {
  const { data, loading } = useQuery<SystemMetricsResp>(CLUSTER_SYSTEM_METRICS, {
    variables: {
      clusterId,
      rangeSeconds: win.rangeSeconds,
      stepSeconds: win.stepSeconds,
    },
    pollInterval: 60_000,
  });

  const metrics = data?.astroliftClusterSystemMetrics ?? null;
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
        <SystemMetricsUnavailableCard reason={metrics?.reason ?? null} providerSlug={providerSlug} />
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
        <span className="text-muted-foreground text-xs tracking-wide uppercase">{series.label}</span>
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

// ─── Window selector ──────────────────────────────────────────────────
function WindowSelector({
  value,
  onChange,
}: {
  value: WindowLabel;
  onChange: (w: WindowLabel) => void;
}) {
  return (
    <div className="bg-muted/40 inline-flex rounded-md border p-0.5">
      {WINDOWS.map((w) => (
        <Button
          key={w.label}
          variant={value === w.label ? "default" : "ghost"}
          size="sm"
          className="h-7 px-3 text-xs"
          onClick={() => onChange(w.label)}
        >
          {w.label}
        </Button>
      ))}
    </div>
  );
}
