"use client";

import { useState } from "react";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon } from "lucide-react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { ClusterTabs } from "../components/cluster-tabs";

// ─── Window config ────────────────────────────────────────────────────
const WINDOWS = [
  { label: "1h",  rangeSeconds: 3_600,   stepSeconds: 60   },
  { label: "6h",  rangeSeconds: 21_600,  stepSeconds: 300  },
  { label: "24h", rangeSeconds: 86_400,  stepSeconds: 900  },
  { label: "3d",  rangeSeconds: 259_200, stepSeconds: 3600 },
  { label: "7d",  rangeSeconds: 604_800, stepSeconds: 7200 },
] as const;

type WindowLabel = (typeof WINDOWS)[number]["label"];

// ─── Types ────────────────────────────────────────────────────────────
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

interface PrometheusRangeResp {
  astroliftClusterPrometheusRangeMetrics: {
    available: boolean;
    reason: string | null;
    rangeSeconds: number;
    stepSeconds: number;
    series: RangeSeries[];
  };
}

interface PrometheusInstantResp {
  astroliftClusterPrometheusMetrics: {
    available: boolean;
    reason: string | null;
    nodeCount: number | null;
    podRunningRatio: number | null;
    cpuUtilization: number | null;
    memoryUtilization: number | null;
    deploymentReadyRatio: number | null;
  };
}

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

// ─── Helpers ──────────────────────────────────────────────────────────
function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtValue(value: number | null, unit: string): string {
  if (value === null) return "—";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "count") return String(Math.round(value));
  return value.toFixed(2);
}

function seriesTone(
  series: RangeSeries,
): "ok" | "warn" | "bad" | "neutral" {
  const v = series.current;
  if (v === null) return "neutral";
  if (series.unit === "count") return "neutral";
  if (series.metric === "pod_running_ratio" || series.metric === "deployment_ready_ratio") {
    if (v >= 0.9) return "ok";
    if (v >= 0.7) return "warn";
    return "bad";
  }
  // utilization metrics — high is bad
  if (v < 0.7) return "ok";
  if (v < 0.9) return "warn";
  return "bad";
}

const TONE_COLORS = {
  ok: { text: "text-emerald-600", stroke: "#10b981", fill: "#d1fae5" },
  warn: { text: "text-amber-600", stroke: "#f59e0b", fill: "#fef3c7" },
  bad: { text: "text-destructive", stroke: "#ef4444", fill: "#fee2e2" },
  neutral: { text: "text-foreground", stroke: "#6366f1", fill: "#e0e7ff" },
};

// ─── Main component ───────────────────────────────────────────────────
export function ClusterStatusClient({ slug }: { slug: string }) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster status" description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!cluster) {
    return (
      <PageShell
        title="Cluster not found"
        description="The cluster doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${cluster.name} · Status`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="status" />
      <ClusterMetricsSection clusterId={cluster.id} />
    </PageShell>
  );
}

// ─── Metrics section with window selector ────────────────────────────
function ClusterMetricsSection({ clusterId }: { clusterId: string }) {
  const [window, setWindow] = useState<WindowLabel>("1h");
  const win = WINDOWS.find((w) => w.label === window)!;

  const { data: rangeData, loading: rangeLoading } =
    useQuery<PrometheusRangeResp>(CLUSTER_PROMETHEUS_RANGE_METRICS, {
      variables: {
        clusterId,
        rangeSeconds: win.rangeSeconds,
        stepSeconds: win.stepSeconds,
      },
      pollInterval: 60000,
    });

  // Instant fallback for the current-value tiles if range hasn't loaded
  const { data: instantData } = useQuery<PrometheusInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    { variables: { clusterId }, pollInterval: 60000 },
  );

  const range = rangeData?.astroliftClusterPrometheusRangeMetrics;
  const instant = instantData?.astroliftClusterPrometheusMetrics;

  // Not yet loaded
  if (rangeLoading && !range) {
    return (
      <div className="space-y-4">
        <WindowSelector value={window} onChange={setWindow} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      </div>
    );
  }

  // Prometheus unavailable
  if (!range?.available) {
    const reason = range?.reason ?? instant?.reason;
    return (
      <div className="space-y-4">
        <WindowSelector value={window} onChange={setWindow} />
        <Card className="!rounded-none shadow-md">
          <CardContent className="pt-6">
            <p className="text-muted-foreground text-sm">
              {reason === "no_endpoint"
                ? "No Prometheus endpoint configured. Run Refresh cluster management to auto-discover it, or set prometheus_endpoint in provider_config."
                : "Prometheus is unreachable. Check that the endpoint is accessible from the control plane."}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <WindowSelector value={window} onChange={setWindow} />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {range.series.map((s) => (
          <MetricSparklineCard key={s.metric} series={s} />
        ))}
      </div>
    </div>
  );
}

// ─── Window selector ─────────────────────────────────────────────────
function WindowSelector({
  value,
  onChange,
}: {
  value: WindowLabel;
  onChange: (w: WindowLabel) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      {WINDOWS.map((w) => (
        <Button
          key={w.label}
          variant={value === w.label ? "secondary" : "ghost"}
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

// ─── Single metric sparkline card ────────────────────────────────────
function MetricSparklineCard({ series }: { series: RangeSeries }) {
  const tone = seriesTone(series);
  const colors = TONE_COLORS[tone];
  const chartData = series.points.map((p) => ({ ts: p.ts, value: p.value }));

  return (
    <Card className="!rounded-none shadow-md overflow-hidden">
      <CardHeader className="pb-2 pt-4 px-4">
        <span className="text-muted-foreground text-xs uppercase tracking-wide">
          {series.label}
        </span>
        <CardTitle
          className={`text-2xl font-semibold tabular-nums ${colors.text}`}
        >
          {fmtValue(series.current, series.unit)}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {chartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={72}>
            <AreaChart
              data={chartData}
              margin={{ top: 0, right: 0, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient
                  id={`grad-${series.metric}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="5%"
                    stopColor={colors.stroke}
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="95%"
                    stopColor={colors.stroke}
                    stopOpacity={0.0}
                  />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="ts"
                hide
                type="number"
                domain={["dataMin", "dataMax"]}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const pt = payload[0].payload as RangePoint;
                  return (
                    <div className="bg-popover border rounded px-2 py-1 text-xs shadow-md">
                      <div className="font-mono text-muted-foreground">
                        {fmtTs(pt.ts)}
                      </div>
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
                fill={`url(#grad-${series.metric})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[72px] flex items-center justify-center">
            <span className="text-muted-foreground text-xs">
              {series.points.length === 0
                ? "No data in window"
                : "Collecting data…"}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
