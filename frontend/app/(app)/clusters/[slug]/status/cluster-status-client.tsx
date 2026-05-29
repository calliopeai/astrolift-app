"use client";

import { useState } from "react";

import Link from "next/link";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  CheckCircle2Icon,
  CircleDotIcon,
  ClockIcon,
  GitBranchIcon,
  InfoIcon,
  Loader2Icon,
  RefreshCwIcon,
  ServerIcon,
  ServerOffIcon,
  WifiOffIcon,
  XCircleIcon,
} from "lucide-react";
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
  CLUSTER_HEALTH,
  CLUSTER_LIFECYCLE_AUDIT,
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
  CLUSTER_WORKLOAD_HEALTH,
  LIST_CLUSTERS,
  RECENT_CLUSTER_WORKFLOWS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { formatRelativeAge } from "@/lib/format";

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

interface PodPhase {
  namespace: string;
  phase: string;
  count: number;
}

interface ClusterEvent {
  namespace: string;
  name: string;
  reason: string;
  message: string;
  type: string;
  count: number;
  firstSeen: string;
  lastSeen: string;
  involvedObject: string;
}

interface ClusterHealthResp {
  astroliftClusterHealth: {
    clusterId: string;
    pods: PodPhase[];
    events: ClusterEvent[];
  } | null;
}

interface WorkloadRow {
  namespace: string;
  workloadName: string;
  desiredReplicas: number;
  readyReplicas: number;
  restartCount24h: number;
  lastImageDeployedAt: string;
}

interface WorkloadHealthResp {
  astroliftClusterWorkloadHealth: WorkloadRow[];
}

interface WorkflowRun {
  workflowId: string;
  workflowType: string;
  status: string;
  startedAt: string;
  closedAt: string;
  runId: string;
}

interface WorkflowsResp {
  astroliftRecentClusterWorkflows: WorkflowRun[];
}

interface AuditRow {
  operation: string;
  variables: Record<string, unknown>;
  success: boolean;
  errors: string[];
  timestamp: string;
  actor: string | null;
}

interface LifecycleResp {
  astroliftClusterLifecycleAudit: AuditRow[];
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
  if (unit === "count") return value < 0.1 ? "0" : value.toFixed(2);
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

function seriesTone(
  series: RangeSeries,
): "ok" | "warn" | "bad" | "neutral" {
  const v = series.current;
  if (v === null) return "neutral";
  if (series.metric === "pod_running_ratio" || series.metric === "deployment_ready_ratio") {
    if (v >= 0.9) return "ok";
    if (v >= 0.7) return "warn";
    return "bad";
  }
  // latency — low is good; apiserver p99 > 500ms is concerning
  if (series.metric === "latency_p99") {
    if (v < 0.1) return "ok";
    if (v < 0.5) return "warn";
    return "bad";
  }
  // network throughput — neutral (volume isn't inherently bad)
  if (series.metric === "network_rx") return "neutral";
  // restart rate — any restarts are concerning
  if (series.metric === "restart_rate") {
    if (v === 0) return "ok";
    if (v < 1) return "warn";
    return "bad";
  }
  if (series.unit === "count") return "neutral";
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

type Tone = keyof typeof TONE_COLORS;

// Pretty-print a CamelCase workflow type ("InstallClusterPrereqs" →
// "Install cluster prereqs"). Keeps acronyms readable by treating
// runs of caps as a single word.
function prettyWorkflowType(t: string): string {
  if (!t) return "—";
  const spaced = t
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z\d])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

// Pretty-print a GraphQL mutation operation name ("registerCluster"
// → "Register cluster"). First letter capitalised, the rest lowercased
// after camel-case splits.
function prettyOperation(op: string): string {
  if (!op) return "—";
  const spaced = op.replace(/([a-z\d])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

// Duration between two ISO timestamps, formatted as "12s" / "4m 13s"
// / "1h 7m". Returns null when either timestamp is empty/unparseable
// (e.g. a workflow that hasn't closed yet).
function fmtDuration(startedAt: string, closedAt: string): string | null {
  if (!startedAt || !closedAt) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(closedAt);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remSec = seconds % 60;
  if (minutes < 60) return remSec ? `${minutes}m ${remSec}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin ? `${hours}h ${remMin}m` : `${hours}h`;
}

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
      <div className="space-y-4">
        <ClusterMetricsSection clusterId={cluster.id} slug={slug} />
        <WorkloadHealthSection clusterId={cluster.id} />
        <LiveHealthSection clusterId={cluster.id} />
        <RecentWorkflowsSection clusterId={cluster.id} />
        <LifecycleSection clusterId={cluster.id} />
      </div>
    </PageShell>
  );
}

// ─── Section card template ───────────────────────────────────────────
function SectionCard({
  icon,
  title,
  subtitle,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="!rounded-none shadow-md">
      <CardHeader className="pb-3 pt-4 px-4">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 text-muted-foreground">{icon}</span>
          <div className="flex-1">
            <CardTitle className="text-sm font-semibold">{title}</CardTitle>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4">{children}</CardContent>
    </Card>
  );
}

// ─── Metrics section: KPI bar + sparkline grid ────────────────────────
function ClusterMetricsSection({
  clusterId,
  slug,
}: {
  clusterId: string;
  slug: string;
}) {
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

  const { data: instantData, loading: instantLoading } =
    useQuery<PrometheusInstantResp>(CLUSTER_PROMETHEUS_METRICS, {
      variables: { clusterId },
      pollInterval: 60000,
    });

  const range = rangeData?.astroliftClusterPrometheusRangeMetrics;
  const instant = instantData?.astroliftClusterPrometheusMetrics;

  return (
    <SectionCard
      icon={<BarChart3Icon className="size-4" />}
      title="Cluster saturation"
      subtitle="Prometheus-sourced golden signals — current snapshot and historical trend."
      action={<WindowSelector value={window} onChange={setWindow} />}
    >
      <SaturationKPIBar
        instant={instant ?? null}
        loading={instantLoading && !instant}
        slug={slug}
      />
      <div className="mt-5">
        <SparklineGrid
          range={range ?? null}
          loading={rangeLoading && !range}
          slug={slug}
          instantReason={instant?.reason ?? null}
        />
      </div>
    </SectionCard>
  );
}

// ─── Saturation KPI bar ──────────────────────────────────────────────
function SaturationKPIBar({
  instant,
  loading,
  slug,
}: {
  instant: PrometheusInstantResp["astroliftClusterPrometheusMetrics"] | null;
  loading: boolean;
  slug: string;
}) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (!instant?.available) {
    return (
      <PrometheusUnavailableCard
        isNoEndpoint={instant?.reason === "no_endpoint"}
        settingsHref={`/clusters/${slug}/settings`}
      />
    );
  }

  const kpis: { label: string; value: number | null; unit: string; tone: Tone }[] = [
    {
      label: "CPU utilization",
      value: instant.cpuUtilization,
      unit: "ratio",
      tone: utilizationTone(instant.cpuUtilization),
    },
    {
      label: "Memory utilization",
      value: instant.memoryUtilization,
      unit: "ratio",
      tone: utilizationTone(instant.memoryUtilization),
    },
    {
      label: "Nodes",
      value: instant.nodeCount,
      unit: "count",
      tone: "neutral",
    },
    {
      label: "Pod health",
      value: instant.podRunningRatio,
      unit: "ratio",
      tone: healthTone(instant.podRunningRatio),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      {kpis.map((k) => (
        <div key={k.label} className="space-y-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            {k.label}
          </p>
          <p
            className={`text-2xl font-semibold tabular-nums ${TONE_COLORS[k.tone].text}`}
          >
            {fmtValue(k.value, k.unit)}
          </p>
        </div>
      ))}
    </div>
  );
}

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

// ─── Sparkline grid ──────────────────────────────────────────────────
function SparklineGrid({
  range,
  loading,
  slug,
  instantReason,
}: {
  range: PrometheusRangeResp["astroliftClusterPrometheusRangeMetrics"] | null;
  loading: boolean;
  slug: string;
  instantReason: string | null;
}) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-32 w-full" />
        ))}
      </div>
    );
  }

  if (!range?.available) {
    const reason = range?.reason ?? instantReason;
    const isNoEndpoint = reason === "no_endpoint";
    return (
      <PrometheusUnavailableCard
        isNoEndpoint={isNoEndpoint}
        settingsHref={`/clusters/${slug}/settings`}
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {range.series.map((s) => (
        <MetricSparklineCard key={s.metric} series={s} />
      ))}
    </div>
  );
}

// ─── Prometheus unavailable card ─────────────────────────────────────
function PrometheusUnavailableCard({
  isNoEndpoint,
  settingsHref,
}: {
  isNoEndpoint: boolean;
  settingsHref: string;
}) {
  const Icon = isNoEndpoint ? RefreshCwIcon : WifiOffIcon;
  const title = isNoEndpoint
    ? "No Prometheus endpoint"
    : "Prometheus unreachable";
  const body = isNoEndpoint
    ? "The control plane hasn't discovered a Prometheus endpoint for this cluster yet."
    : "The control plane can't reach the Prometheus endpoint stored for this cluster.";
  const hint = isNoEndpoint
    ? "Go to Settings and run Refresh cluster management to auto-discover the endpoint, or set prometheus_endpoint in provider_config."
    : "Verify the endpoint is accessible from the control plane on port 9090 and that firewall rules allow inbound traffic from the ECS task security group.";

  return (
    <div className="flex items-start gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
      <Icon className="mt-0.5 size-5 shrink-0 text-amber-500" />
      <div className="space-y-1">
        <p className="font-medium text-sm">{title}</p>
        <p className="text-muted-foreground text-sm">{body}</p>
        <p className="text-muted-foreground text-xs">{hint}</p>
        {isNoEndpoint && (
          <Link
            href={settingsHref}
            className="mt-2 inline-block text-xs text-primary underline-offset-4 hover:underline"
          >
            Go to cluster settings →
          </Link>
        )}
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
          <ResponsiveContainer width="100%" height={80}>
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
          <div className="h-[80px] flex items-center justify-center">
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

// ─── Workload health section ─────────────────────────────────────────
function WorkloadHealthSection({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<WorkloadHealthResp>(
    CLUSTER_WORKLOAD_HEALTH,
    {
      variables: { clusterId },
      pollInterval: 30000,
    },
  );
  const rows = data?.astroliftClusterWorkloadHealth ?? [];

  // Sort most-broken first within each namespace; namespaces are
  // grouped alphabetically.
  const grouped = new Map<string, WorkloadRow[]>();
  for (const r of rows) {
    const list = grouped.get(r.namespace) ?? [];
    list.push(r);
    grouped.set(r.namespace, list);
  }
  for (const list of grouped.values()) {
    list.sort((a, b) => {
      const deficitA = a.desiredReplicas - a.readyReplicas;
      const deficitB = b.desiredReplicas - b.readyReplicas;
      if (deficitA !== deficitB) return deficitB - deficitA;
      return a.workloadName.localeCompare(b.workloadName);
    });
  }
  const namespaces = Array.from(grouped.keys()).sort();

  return (
    <SectionCard
      icon={<ServerIcon className="size-4" />}
      title="Workload health"
      subtitle="Per-Deployment readiness + 24h restart counts. Sorted most-broken first; polls every 30s."
    >
      {loading && rows.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyHint
          icon={<ServerOffIcon className="size-4" />}
          text="No deployment data — apiserver unreachable or no workloads running yet."
        />
      ) : (
        <div className="space-y-4">
          {namespaces.map((ns) => (
            <div key={ns}>
              <p className="mb-1.5 font-mono text-[11px] text-muted-foreground">
                {ns}/
              </p>
              <div>
                {grouped.get(ns)!.map((row) => (
                  <WorkloadRowItem key={row.workloadName} row={row} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function WorkloadRowItem({ row }: { row: WorkloadRow }) {
  const deficit = row.desiredReplicas - row.readyReplicas;
  const readyTone =
    deficit === 0
      ? "text-emerald-600"
      : deficit === row.desiredReplicas
        ? "text-destructive"
        : "text-amber-600";

  const deployedAge = row.lastImageDeployedAt
    ? formatRelativeAge(row.lastImageDeployedAt)
    : null;

  return (
    <div className="flex items-center gap-3 border-b border-border/50 py-2 text-sm last:border-0">
      <code className="flex-1 truncate font-mono text-xs">
        {row.workloadName}
      </code>
      <span className={`tabular-nums text-xs ${readyTone}`}>
        {row.readyReplicas} / {row.desiredReplicas}
      </span>
      {row.restartCount24h > 0 ? (
        <Badge
          variant="outline"
          className="border-amber-500/40 text-amber-600 dark:text-amber-500"
        >
          {row.restartCount24h} restart{row.restartCount24h === 1 ? "" : "s"}
        </Badge>
      ) : (
        <span className="text-[11px] text-muted-foreground/60">
          no restarts
        </span>
      )}
      <span className="w-20 text-right font-mono text-[11px] text-muted-foreground">
        {deployedAge ?? "—"}
      </span>
    </div>
  );
}

// ─── Live health section ─────────────────────────────────────────────
const POD_PHASE_TONE: Record<string, Tone> = {
  Running: "ok",
  Succeeded: "neutral",
  Pending: "warn",
  Failed: "bad",
  CrashLoopBackOff: "bad",
  Unknown: "neutral",
};

function LiveHealthSection({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<ClusterHealthResp>(CLUSTER_HEALTH, {
    variables: { clusterId, eventLimit: 20 },
    pollInterval: 30000,
  });
  const payload = data?.astroliftClusterHealth;
  const pods = payload?.pods ?? [];
  const events = payload?.events ?? [];

  // Aggregate pod counts by phase across all namespaces — the live
  // view answers "is the cluster green?" before "where is it red?".
  const phaseTotals = new Map<string, number>();
  for (const p of pods) {
    phaseTotals.set(p.phase, (phaseTotals.get(p.phase) ?? 0) + p.count);
  }
  const phaseOrder = ["Running", "Pending", "Failed", "CrashLoopBackOff", "Succeeded", "Unknown"];
  const phasesSorted = Array.from(phaseTotals.entries()).sort(
    ([a], [b]) => phaseOrder.indexOf(a) - phaseOrder.indexOf(b),
  );

  return (
    <SectionCard
      icon={<ActivityIcon className="size-4" />}
      title="Live health"
      subtitle="Pod-phase rollup + recent warning events from the cluster driver. Polls every 30s."
    >
      {loading && pods.length === 0 && events.length === 0 ? (
        <Skeleton className="h-24 w-full" />
      ) : pods.length === 0 && events.length === 0 ? (
        <EmptyHint
          icon={<ServerOffIcon className="size-4" />}
          text="No health data — driver couldn't reach the apiserver."
        />
      ) : (
        <div className="space-y-4">
          <div>
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Pod phases
            </p>
            {phasesSorted.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No pods in managed namespaces.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {phasesSorted.map(([phase, count]) => (
                  <PodPhasePill key={phase} phase={phase} count={count} />
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              Recent events
            </p>
            {events.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No recent warning events. (A quiet event feed is the
                expected baseline.)
              </p>
            ) : (
              <div>
                {events.map((e, i) => (
                  <EventRow key={`${e.namespace}-${e.name}-${i}`} event={e} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

function PodPhasePill({ phase, count }: { phase: string; count: number }) {
  const tone = POD_PHASE_TONE[phase] ?? "neutral";
  const classes: Record<Tone, string> = {
    ok: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30 dark:text-emerald-400",
    warn: "bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-400",
    bad: "bg-destructive/10 text-destructive border-destructive/30",
    neutral: "bg-muted text-muted-foreground border-border",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${classes[tone]}`}
    >
      <span className="font-medium">{phase}</span>
      <span className="tabular-nums opacity-80">{count}</span>
    </span>
  );
}

function EventRow({ event }: { event: ClusterEvent }) {
  const isWarning = event.type === "Warning";
  const Icon = isWarning ? AlertTriangleIcon : InfoIcon;
  const iconClass = isWarning ? "text-amber-500" : "text-muted-foreground";
  const lastSeen = event.lastSeen ? formatRelativeAge(event.lastSeen) : "";

  return (
    <div className="flex items-center gap-3 border-b border-border/50 py-2 text-sm last:border-0">
      <Icon className={`size-3.5 shrink-0 ${iconClass}`} />
      <code className="shrink-0 font-mono text-[11px]">{event.reason}</code>
      <span className="flex-1 truncate text-xs text-muted-foreground">
        {event.involvedObject}
        {event.message && (
          <>
            <span className="opacity-60"> · </span>
            <span>{event.message}</span>
          </>
        )}
      </span>
      {event.count > 1 && (
        <Badge variant="outline" className="text-[10px]">
          ×{event.count}
        </Badge>
      )}
      <span className="w-16 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
        {lastSeen || "—"}
      </span>
    </div>
  );
}

// ─── Recent workflows section ────────────────────────────────────────
function RecentWorkflowsSection({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<WorkflowsResp>(RECENT_CLUSTER_WORKFLOWS, {
    variables: { clusterId, limit: 10 },
  });
  const runs = data?.astroliftRecentClusterWorkflows ?? [];

  return (
    <SectionCard
      icon={<GitBranchIcon className="size-4" />}
      title="Recent workflow runs"
      subtitle="Temporal runs targeting this cluster — most recent first."
    >
      {loading && runs.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : runs.length === 0 ? (
        <EmptyHint
          icon={<GitBranchIcon className="size-4" />}
          text="No workflow runs recorded yet."
        />
      ) : (
        <div>
          {runs.map((r) => (
            <WorkflowRunRow key={r.workflowId + r.runId} run={r} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function WorkflowRunRow({ run }: { run: WorkflowRun }) {
  const { Icon, iconClass, label } = workflowStatusIcon(run.status);
  const startedAge = run.startedAt ? formatRelativeAge(run.startedAt) : "";
  const duration = fmtDuration(run.startedAt, run.closedAt);

  return (
    <div className="flex items-center gap-3 border-b border-border/50 py-2 text-sm last:border-0">
      <Icon className={`size-4 shrink-0 ${iconClass}`} />
      <span className="flex-1 truncate font-medium">
        {prettyWorkflowType(run.workflowType)}
      </span>
      <Badge variant="outline" className="text-[10px]">
        {label}
      </Badge>
      {duration && (
        <span className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
          <ClockIcon className="size-3" />
          {duration}
        </span>
      )}
      <span className="w-20 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
        {startedAge || "—"}
      </span>
    </div>
  );
}

function workflowStatusIcon(status: string): {
  Icon: React.ComponentType<{ className?: string }>;
  iconClass: string;
  label: string;
} {
  const norm = status.toUpperCase();
  if (norm === "COMPLETED") {
    return {
      Icon: CheckCircle2Icon,
      iconClass: "text-emerald-600",
      label: "Completed",
    };
  }
  if (norm === "RUNNING") {
    return {
      Icon: Loader2Icon,
      iconClass: "animate-spin text-amber-600",
      label: "Running",
    };
  }
  if (norm === "FAILED" || norm === "TIMED_OUT" || norm === "TERMINATED") {
    return {
      Icon: XCircleIcon,
      iconClass: "text-destructive",
      label: norm === "FAILED"
        ? "Failed"
        : norm === "TIMED_OUT"
          ? "Timed out"
          : "Terminated",
    };
  }
  return {
    Icon: CircleDotIcon,
    iconClass: "text-muted-foreground",
    label: prettyOperation(norm),
  };
}

// ─── Lifecycle timeline section ──────────────────────────────────────
function LifecycleSection({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<LifecycleResp>(CLUSTER_LIFECYCLE_AUDIT, {
    variables: { clusterId, limit: 20 },
  });
  const entries = data?.astroliftClusterLifecycleAudit ?? [];

  return (
    <SectionCard
      icon={<ClockIcon className="size-4" />}
      title="Lifecycle events"
      subtitle="Mutations targeting this cluster — registered → managing → managed transitions, refreshes, decommissions."
    >
      {loading && entries.length === 0 ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : entries.length === 0 ? (
        <EmptyHint
          icon={<ClockIcon className="size-4" />}
          text="No lifecycle events recorded yet."
        />
      ) : (
        <div>
          {entries.map((e, i) => (
            <LifecycleRow key={`${e.timestamp}-${i}`} entry={e} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function LifecycleRow({ entry }: { entry: AuditRow }) {
  const ts = entry.timestamp ? formatRelativeAge(entry.timestamp) : "";
  const dotClass = entry.success
    ? "text-emerald-500"
    : "text-destructive";

  return (
    <div className="flex items-center gap-3 border-b border-border/50 py-2 text-sm last:border-0">
      <span className={`shrink-0 text-base leading-none ${dotClass}`}>●</span>
      <span className="flex-1 truncate font-medium">
        {prettyOperation(entry.operation)}
      </span>
      {entry.actor && (
        <span className="text-[11px] text-muted-foreground">
          by{" "}
          <span className="font-mono">{entry.actor}</span>
        </span>
      )}
      {!entry.success && entry.errors.length > 0 && (
        <span
          className="max-w-[40%] truncate text-[11px] text-destructive"
          title={entry.errors[0]}
        >
          {entry.errors[0]}
        </span>
      )}
      <span className="w-20 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
        {ts || "—"}
      </span>
    </div>
  );
}

// ─── Shared empty hint ───────────────────────────────────────────────
function EmptyHint({
  icon,
  text,
}: {
  icon: React.ReactNode;
  text: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-dashed border-border/70 px-3 py-4 text-sm text-muted-foreground">
      <span className="text-muted-foreground/70">{icon}</span>
      <span>{text}</span>
    </div>
  );
}
