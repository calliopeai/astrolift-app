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
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_HEALTH,
  CLUSTER_LIFECYCLE_AUDIT,
  CLUSTER_LIVE_STATE,
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
  CLUSTER_WORKLOAD_HEALTH,
  LIST_CLUSTERS,
  RECENT_CLUSTER_WORKFLOWS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import {
  clusterOfflineMessage,
  formatHeartbeatAge,
  heartbeatPresentation,
  isClusterLive,
  type ClusterLiveState,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { formatRelativeAge } from "@/lib/format";

import { ClusterTabs } from "../components/cluster-tabs";

// ─── Window config ────────────────────────────────────────────────────
const WINDOWS = [
  { label: "1h", rangeSeconds: 3_600, stepSeconds: 60 },
  { label: "6h", rangeSeconds: 21_600, stepSeconds: 300 },
  { label: "24h", rangeSeconds: 86_400, stepSeconds: 900 },
  { label: "3d", rangeSeconds: 259_200, stepSeconds: 3600 },
  { label: "7d", rangeSeconds: 604_800, stepSeconds: 7200 },
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

interface LiveStateResp {
  astroliftClusterLiveState: ClusterLiveState | null;
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

function seriesTone(series: RangeSeries): "ok" | "warn" | "bad" | "neutral" {
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
  ok: { text: "text-success-fg", stroke: "var(--success)", fill: "var(--success-bg)" },
  warn: { text: "text-warning-fg", stroke: "var(--warning)", fill: "var(--warning-bg)" },
  bad: { text: "text-destructive", stroke: "var(--danger)", fill: "var(--danger-bg)" },
  neutral: { text: "text-foreground", stroke: "var(--info)", fill: "var(--info-bg)" },
};

type Tone = keyof typeof TONE_COLORS;

// Pretty-print a CamelCase workflow type ("InstallClusterPrereqs" →
// "Install cluster prereqs"). Keeps acronyms readable by treating
// runs of caps as a single word.
function prettyWorkflowType(t: string): string {
  if (!t) return "—";
  const spaced = t.replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2").replace(/([a-z\d])([A-Z])/g, "$1 $2");
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
      <ClusterStatusSections clusterId={cluster.id} slug={slug} />
    </PageShell>
  );
}

// The driver-dependent cards (saturation, workload health, live health,
// recent workflows) each fire a synchronous driver / Prometheus /
// Temporal call. When the cluster is offline those calls hang until they
// time out, which is exactly the "tabs spin forever" problem the
// keep-alive heartbeat (#808) was built to short-circuit. So we read the
// cheap live state once here and, when the cluster isn't reachable,
// render a targeted offline placeholder INSTEAD of mounting the section —
// the section component owns the useQuery, so not mounting it means the
// query never fires. The Lifecycle timeline is sourced from the audit log
// (not the cluster), so it always renders.
function ClusterStatusSections({ clusterId, slug }: { clusterId: string; slug: string }) {
  const { data } = useQuery<LiveStateResp>(CLUSTER_LIVE_STATE, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  const state = data?.astroliftClusterLiveState ?? null;
  const status: HeartbeatStatus = state?.status ?? "never_seen";
  const age = state?.heartbeatAgeSeconds ?? null;
  const live = isClusterLive(status);

  return (
    <div className="space-y-4">
      <LiveStateSection clusterId={clusterId} slug={slug} />
      {live ? (
        <>
          <ClusterMetricsSection clusterId={clusterId} slug={slug} />
          <WorkloadHealthSection clusterId={clusterId} />
          <LiveHealthSection clusterId={clusterId} />
          <RecentWorkflowsSection clusterId={clusterId} />
        </>
      ) : (
        <>
          <OfflineCard
            icon={<BarChart3Icon className="size-4" />}
            title="Cluster saturation"
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<ServerIcon className="size-4" />}
            title="Workload health"
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<ActivityIcon className="size-4" />}
            title="Live health"
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<GitBranchIcon className="size-4" />}
            title="Recent workflows"
            status={status}
            age={age}
            slug={slug}
          />
        </>
      )}
      <LifecycleSection clusterId={clusterId} />
    </div>
  );
}

// Offline stand-in for a driver-dependent card. Rendered in place of the
// real section while the cluster is offline / has no agent, so the
// section's live query is never mounted. Names the cluster's last-seen
// cue and links to settings (where the agent is installed / rotated).
function OfflineCard({
  icon,
  title,
  status,
  age,
  slug,
}: {
  icon: React.ReactNode;
  title: string;
  status: HeartbeatStatus;
  age: number | null;
  slug: string;
}) {
  return (
    <SectionCard
      icon={icon}
      title={title}
      action={
        <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
          {status === "never_seen" ? (
            <ServerOffIcon className="size-3.5" />
          ) : (
            <WifiOffIcon className="size-3.5" />
          )}
          {status === "never_seen" ? "No agent" : "Offline"}
        </span>
      }
    >
      <div className="border-border/70 text-muted-foreground flex items-start gap-3 rounded-md border border-dashed px-3 py-4 text-sm">
        <WifiOffIcon className="text-muted-foreground/70 mt-0.5 size-4 shrink-0" />
        <div className="space-y-1">
          <p>{clusterOfflineMessage(status, age)}</p>
          <Link
            href={`/clusters/${slug}/settings`}
            className="text-primary inline-block text-xs underline-offset-4 hover:underline"
          >
            Check cluster settings →
          </Link>
        </div>
      </div>
    </SectionCard>
  );
}

// ─── Live keep-alive state section (#808) ─────────────────────────────
// The ONE card that does no driver/Prometheus/Temporal call — it reads
// the persisted heartbeat. It's the always-renders signal: a status
// badge + last-seen age + the agent's last self-reported snapshot
// (nodes, pods, CPU/mem, ingress IPs). When the cluster is offline this
// is what tells the operator "the cluster stopped talking to us X ago"
// instead of every card below spinning forever.
function LiveStateSection({ clusterId, slug }: { clusterId: string; slug: string }) {
  const { data, loading } = useQuery<LiveStateResp>(CLUSTER_LIVE_STATE, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  const state = data?.astroliftClusterLiveState ?? null;
  const status: HeartbeatStatus = state?.status ?? "never_seen";
  const p = heartbeatPresentation(status);
  const age = formatHeartbeatAge(state?.heartbeatAgeSeconds ?? null);
  const live = isClusterLive(status);

  if (loading && !state) {
    return (
      <SectionCard
        icon={<ActivityIcon className="size-4" />}
        title="Cluster connection"
        subtitle="Keep-alive heartbeat from the in-cluster agent."
      >
        <Skeleton className="h-16 w-full" />
      </SectionCard>
    );
  }

  return (
    <SectionCard
      icon={<ActivityIcon className="size-4" />}
      title="Cluster connection"
      subtitle="Keep-alive heartbeat from the in-cluster agent. Polls every 30s."
      action={
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${p.pill}`}
        >
          {live ? (
            <CircleDotIcon className="size-3" />
          ) : status === "never_seen" ? (
            <ServerOffIcon className="size-3" />
          ) : (
            <WifiOffIcon className="size-3" />
          )}
          {p.label}
        </span>
      }
    >
      {status === "never_seen" ? (
        <div className="border-border/70 flex items-start gap-3 rounded-md border border-dashed p-3">
          <ServerOffIcon className="text-muted-foreground mt-0.5 size-5 shrink-0" />
          <div className="space-y-1 text-sm">
            <p className="font-medium">No keep-alive agent</p>
            <p className="text-muted-foreground">
              No agent has reported from this cluster yet. Install the keep-alive agent to surface
              live pod, node, and resource state here.
            </p>
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary mt-1 inline-block text-xs underline-offset-4 hover:underline"
            >
              Set up the cluster agent →
            </Link>
          </div>
        </div>
      ) : !live ? (
        <div className="border-destructive/30 bg-destructive/5 flex items-start gap-3 rounded-md border p-3">
          <WifiOffIcon className="text-destructive mt-0.5 size-5 shrink-0" />
          <div className="space-y-1 text-sm">
            <p className="font-medium">Cluster disconnected</p>
            <p className="text-muted-foreground">
              {clusterOfflineMessage(status, state?.heartbeatAgeSeconds ?? null)} The live cards
              below may be empty or stale until the agent reconnects.
            </p>
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary mt-1 inline-block text-xs underline-offset-4 hover:underline"
            >
              Check cluster settings →
            </Link>
          </div>
        </div>
      ) : (
        <LiveSnapshotGrid state={state!} age={age} />
      )}
    </SectionCard>
  );
}

function LiveSnapshotGrid({ state, age }: { state: ClusterLiveState; age: string | null }) {
  // Node readiness — "N/M ready" when the agent reports the ready count
  // (#112), falling back to the bare total for agents that predate it.
  const nodeValue: React.ReactNode =
    state.nodeReadyCount !== null && state.nodeCount !== null ? (
      <>
        {state.nodeReadyCount}/{state.nodeCount}
        <span className="text-muted-foreground ml-1 text-xs font-normal">ready</span>
      </>
    ) : state.nodeCount !== null ? (
      String(state.nodeCount)
    ) : (
      "—"
    );

  const tiles: { label: string; value: React.ReactNode }[] = [
    { label: "Last heartbeat", value: age ?? "—" },
    { label: "Nodes", value: nodeValue },
    {
      label: "Pods",
      value: state.podTotal !== null ? String(state.podTotal) : "—",
    },
    {
      label: "CPU",
      value: state.cpuUtilization !== null ? `${(state.cpuUtilization * 100).toFixed(0)}%` : "—",
    },
    {
      label: "Memory",
      value:
        state.memoryUtilization !== null ? `${(state.memoryUtilization * 100).toFixed(0)}%` : "—",
    },
  ];

  // Per-app pod readiness from the heartbeat payload, keyed by app slug
  // (#112). Only present here when the cluster is online — this grid is
  // mounted only in the live branch of LiveStateSection, so it never
  // shows stale readiness for an offline / never-seen cluster.
  const appEntries = Object.entries(state.appReadiness ?? {}).sort(([a], [b]) =>
    a.localeCompare(b)
  );

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {tiles.map((t) => (
          <div key={t.label} className="space-y-1">
            <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
              {t.label}
            </p>
            <p className="text-xl font-semibold tabular-nums">{t.value}</p>
          </div>
        ))}
      </div>
      {appEntries.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
            App readiness
          </p>
          <div className="flex flex-wrap gap-2">
            {appEntries.map(([appSlug, r]) => (
              <AppReadinessPill key={appSlug} appSlug={appSlug} ready={r.ready} total={r.total} />
            ))}
          </div>
        </div>
      )}
      {state.ingressIps.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Ingress:</span>
          {state.ingressIps.map((ip) => (
            <code key={ip} className="bg-muted text-2xs rounded px-1.5 py-0.5 font-mono">
              {ip}
            </code>
          ))}
        </div>
      )}
      {state.agentVersion && (
        <p className="text-muted-foreground text-2xs">Agent {state.agentVersion}</p>
      )}
    </div>
  );
}

// Per-app pod readiness pill (#112). Tone tracks the ready/total ratio —
// green when fully ready, red when nothing is ready, amber in between.
// Mirrors the PodPhasePill shape so the live cards read consistently.
function AppReadinessPill({
  appSlug,
  ready,
  total,
}: {
  appSlug: string;
  ready: number;
  total: number;
}) {
  const tone: Tone = total === 0 ? "neutral" : ready >= total ? "ok" : ready === 0 ? "bad" : "warn";
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
      <code className="font-mono font-medium">{appSlug}</code>
      <span className="tabular-nums opacity-80">
        {ready}/{total} ready
      </span>
    </span>
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
      <CardHeader className="px-4 pt-4 pb-3">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground mt-0.5">{icon}</span>
          <div className="flex-1">
            <CardTitle className="text-sm font-semibold">{title}</CardTitle>
            {subtitle && <p className="text-muted-foreground text-xs">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4">{children}</CardContent>
    </Card>
  );
}

// ─── Metrics section: KPI bar + sparkline grid ────────────────────────
function ClusterMetricsSection({ clusterId, slug }: { clusterId: string; slug: string }) {
  const [window, setWindow] = useState<WindowLabel>("1h");
  const win = WINDOWS.find((w) => w.label === window)!;

  const { data: rangeData, loading: rangeLoading } = useQuery<PrometheusRangeResp>(
    CLUSTER_PROMETHEUS_RANGE_METRICS,
    {
      variables: {
        clusterId,
        rangeSeconds: win.rangeSeconds,
        stepSeconds: win.stepSeconds,
      },
      pollInterval: 60000,
    }
  );

  const { data: instantData, loading: instantLoading } = useQuery<PrometheusInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    {
      variables: { clusterId },
      pollInterval: 60000,
    }
  );

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
        reason={instant?.reason ?? null}
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
          <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
            {k.label}
          </p>
          <p className={`text-2xl font-semibold tabular-nums ${TONE_COLORS[k.tone].text}`}>
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
    return (
      <PrometheusUnavailableCard
        reason={range?.reason ?? instantReason}
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
  reason,
  settingsHref,
}: {
  reason: string | null;
  settingsHref: string;
}) {
  const isNoEndpoint = reason === "no_endpoint";
  // The control plane queries Prometheus over HTTP from outside the
  // cluster, so a ClusterIP or *.svc name is not routable however
  // healthy Prometheus is. Naming that ends an investigation that
  // otherwise finishes at a Prometheus with nothing wrong with it (#1711).
  const isClusterInternal = reason === "cluster_internal_endpoint";
  const Icon = isNoEndpoint ? RefreshCwIcon : WifiOffIcon;
  const title = isNoEndpoint
    ? "No Prometheus endpoint"
    : isClusterInternal
      ? "Prometheus endpoint is cluster-internal"
      : "Prometheus unreachable";
  const body = isNoEndpoint
    ? "The control plane hasn't discovered a Prometheus endpoint for this cluster yet."
    : isClusterInternal
      ? "The stored endpoint is an in-cluster address. The control plane queries Prometheus over HTTP from outside the cluster, where a Service ClusterIP doesn't resolve."
      : "The control plane can't reach the Prometheus endpoint stored for this cluster.";
  const hint = isNoEndpoint
    ? "Go to Settings and run Refresh cluster management to auto-discover the endpoint, or set prometheus_endpoint in provider_config."
    : isClusterInternal
      ? "Put an internal load balancer in front of Prometheus and set prometheus_endpoint to that address."
      : "Verify the endpoint is accessible from the control plane on port 9090 and that firewall rules allow inbound traffic from the ECS task security group.";

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
                <linearGradient id={`grad-${series.metric}`} x1="0" y1="0" x2="0" y2="1">
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
                fill={`url(#grad-${series.metric})`}
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

// ─── Workload health section ─────────────────────────────────────────
function WorkloadHealthSection({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<WorkloadHealthResp>(CLUSTER_WORKLOAD_HEALTH, {
    variables: { clusterId },
    pollInterval: 30000,
  });
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
              <p className="text-muted-foreground text-2xs mb-1.5 font-mono">{ns}/</p>
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

  const deployedAge = row.lastImageDeployedAt ? formatRelativeAge(row.lastImageDeployedAt) : null;

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <code className="flex-1 truncate font-mono text-xs">{row.workloadName}</code>
      <span className={`text-xs tabular-nums ${readyTone}`}>
        {row.readyReplicas} / {row.desiredReplicas}
      </span>
      {row.restartCount24h > 0 ? (
        <Badge variant="outline" className="border-amber-500/40 text-amber-600 dark:text-amber-500">
          {row.restartCount24h} restart{row.restartCount24h === 1 ? "" : "s"}
        </Badge>
      ) : (
        <span className="text-muted-foreground/60 text-2xs">no restarts</span>
      )}
      <span className="text-muted-foreground text-2xs w-20 text-right font-mono">
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
    ([a], [b]) => phaseOrder.indexOf(a) - phaseOrder.indexOf(b)
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
            <p className="text-muted-foreground text-2xs mb-2 font-medium tracking-wider uppercase">
              Pod phases
            </p>
            {phasesSorted.length === 0 ? (
              <p className="text-muted-foreground text-xs">No pods in managed namespaces.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {phasesSorted.map(([phase, count]) => (
                  <PodPhasePill key={phase} phase={phase} count={count} />
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="text-muted-foreground text-2xs mb-2 font-medium tracking-wider uppercase">
              Recent events
            </p>
            {events.length === 0 ? (
              <p className="text-muted-foreground text-xs">
                No recent warning events. (A quiet event feed is the expected baseline.)
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
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <Icon className={`size-3.5 shrink-0 ${iconClass}`} />
      <code className="text-2xs shrink-0 font-mono">{event.reason}</code>
      <span className="text-muted-foreground flex-1 truncate text-xs">
        {event.involvedObject}
        {event.message && (
          <>
            <span className="opacity-60"> · </span>
            <span>{event.message}</span>
          </>
        )}
      </span>
      {event.count > 1 && (
        <Badge variant="outline" className="text-2xs">
          ×{event.count}
        </Badge>
      )}
      <span className="text-muted-foreground text-2xs w-16 shrink-0 text-right font-mono">
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
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <Icon className={`size-4 shrink-0 ${iconClass}`} />
      <span className="flex-1 truncate font-medium">{prettyWorkflowType(run.workflowType)}</span>
      <Badge variant="outline" className="text-2xs">
        {label}
      </Badge>
      {duration && (
        <span className="text-muted-foreground text-2xs inline-flex items-center gap-1 font-mono">
          <ClockIcon className="size-3" />
          {duration}
        </span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
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
      label: norm === "FAILED" ? "Failed" : norm === "TIMED_OUT" ? "Timed out" : "Terminated",
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
  const dotClass = entry.success ? "text-emerald-500" : "text-destructive";

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <span className={`shrink-0 text-base leading-none ${dotClass}`}>●</span>
      <span className="flex-1 truncate font-medium">{prettyOperation(entry.operation)}</span>
      {entry.actor && (
        <span className="text-muted-foreground text-2xs">
          by <span className="font-mono">{entry.actor}</span>
        </span>
      )}
      {!entry.success && entry.errors.length > 0 && (
        <span className="text-destructive text-2xs max-w-[40%] truncate" title={entry.errors[0]}>
          {entry.errors[0]}
        </span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
        {ts || "—"}
      </span>
    </div>
  );
}

// ─── Shared empty hint ───────────────────────────────────────────────
function EmptyHint({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="border-border/70 text-muted-foreground flex items-center gap-2 rounded-md border border-dashed px-3 py-4 text-sm">
      <span className="text-muted-foreground/70">{icon}</span>
      <span>{text}</span>
    </div>
  );
}
