"use client";

/**
 * Cluster > Status (#68, #808): the keep-alive connection card, then the
 * driver-dependent cards (saturation, workload health, live health, recent
 * workflows) or an offline stand-in for each, then the lifecycle timeline.
 *
 * Every card here is a pure view over its hook in this folder. The
 * driver-dependent cards arrive as slots so the container can leave them
 * unmounted (their queries never fire) while the cluster is offline.
 */

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
import Link from "next/link";
import type * as React from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { Panel, PanelGrid, type PanelSpan, SkeletonRows } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  clusterOfflineMessage,
  formatHeartbeatAge,
  heartbeatPresentation,
  isClusterLive,
  type ClusterLiveState,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { formatRelativeAge } from "@/lib/format";

import {
  type AuditRow,
  type ClusterEvent,
  type PrometheusInstant,
  type PrometheusRange,
  type RangePoint,
  type RangeSeries,
  WINDOWS,
  type WindowLabel,
  type WorkflowRun,
  type WorkloadRow,
} from "./types";
import type { useClusterHealth } from "./use-cluster-health";
import type { useClusterLifecycleAudit } from "./use-cluster-lifecycle-audit";
import type { useClusterLiveState } from "./use-cluster-live-state";
import type { useClusterMetrics } from "./use-cluster-metrics";
import type { useClusterWorkloadHealth } from "./use-cluster-workload-health";
import type { useRecentClusterWorkflows } from "./use-recent-cluster-workflows";

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

// ─── Status body ──────────────────────────────────────────────────────
export interface ClusterStatusBodyProps {
  slug: string;
  liveState: ReturnType<typeof useClusterLiveState>;
  /** Driver-dependent cards, mounted only while the cluster is live. */
  metrics: React.ReactNode;
  workloads: React.ReactNode;
  liveHealth: React.ReactNode;
  workflows: React.ReactNode;
  /** Sourced from the audit log, not the cluster, so it always renders. */
  lifecycle: React.ReactNode;
}

// The driver-dependent cards (saturation, workload health, live health,
// recent workflows) each fire a synchronous driver / Prometheus /
// Temporal call. When the cluster is offline those calls hang until they
// time out, which is exactly the "tabs spin forever" problem the
// keep-alive heartbeat (#808) was built to short-circuit. So the cheap
// live state decides here and, when the cluster isn't reachable, a
// targeted offline placeholder renders INSTEAD of the slot — the slot's
// container owns the query, so not rendering it means the query never
// fires. The Lifecycle timeline is sourced from the audit log (not the
// cluster), so it always renders.
export function ClusterStatusBody({
  slug,
  liveState,
  metrics,
  workloads,
  liveHealth,
  workflows,
  lifecycle,
}: ClusterStatusBodyProps) {
  const state = liveState.state;
  const status: HeartbeatStatus = state?.status ?? "never_seen";
  const age = state?.heartbeatAgeSeconds ?? null;
  const live = isClusterLive(status);

  return (
    <PanelGrid>
      <StatusLiveStateCard slug={slug} {...liveState} />
      {live ? (
        <>
          {metrics}
          {workloads}
          {liveHealth}
          {workflows}
        </>
      ) : (
        <>
          <OfflineCard
            span={12}
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
      {lifecycle}
    </PanelGrid>
  );
}

// Offline stand-in for a driver-dependent card. Rendered in place of the
// real section while the cluster is offline / has no agent, so the
// section's live query is never mounted. Names the cluster's last-seen
// cue and links to settings (where the agent is installed / rotated).
function OfflineCard({
  span = 6,
  icon,
  title,
  status,
  age,
  slug,
}: {
  span?: PanelSpan;
  icon: React.ReactNode;
  title: string;
  status: HeartbeatStatus;
  age: number | null;
  slug: string;
}) {
  return (
    <Panel
      span={span}
      icon={icon}
      title={title}
      actions={
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
        <div className="min-w-0 space-y-1">
          <p>{clusterOfflineMessage(status, age)}</p>
          <Link
            href={`/clusters/${slug}/settings`}
            className="text-primary inline-block text-xs underline-offset-4 hover:underline"
          >
            Check cluster settings →
          </Link>
        </div>
      </div>
    </Panel>
  );
}

// ─── Live keep-alive state section (#808) ─────────────────────────────
// The ONE card that does no driver/Prometheus/Temporal call — it reads
// the persisted heartbeat. It's the always-renders signal: a status
// badge + last-seen age + the agent's last self-reported snapshot
// (nodes, pods, CPU/mem, ingress IPs). When the cluster is offline this
// is what tells the operator "the cluster stopped talking to us X ago"
// instead of every card below spinning forever.
export function StatusLiveStateCard({
  slug,
  state,
  loading,
  error,
  refetch,
}: { slug: string } & ReturnType<typeof useClusterLiveState>) {
  const status: HeartbeatStatus = state?.status ?? "never_seen";
  const p = heartbeatPresentation(status);
  const age = formatHeartbeatAge(state?.heartbeatAgeSeconds ?? null);
  const live = isClusterLive(status);

  if ((loading || error) && !state) {
    return (
      <Panel
        icon={<ActivityIcon className="size-4" />}
        title="Cluster connection"
        description="Keep-alive heartbeat from the in-cluster agent."
        loading={loading}
        skeleton={<Skeleton className="h-16 w-full" />}
        error={error}
        onRetry={refetch}
      />
    );
  }

  return (
    <Panel
      icon={<ActivityIcon className="size-4" />}
      title="Cluster connection"
      description="Keep-alive heartbeat from the in-cluster agent. Polls every 30s."
      actions={
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
          <div className="min-w-0 space-y-1 text-sm">
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
          <div className="min-w-0 space-y-1 text-sm">
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
    </Panel>
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
          <div key={t.label} className="min-w-0 space-y-1">
            <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
              {t.label}
            </p>
            <p className="font-mono text-xl font-semibold tabular-nums">{t.value}</p>
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
            <code
              key={ip}
              className="bg-muted text-2xs max-w-full rounded px-1.5 py-0.5 font-mono [overflow-wrap:anywhere]"
            >
              {ip}
            </code>
          ))}
        </div>
      )}
      {state.agentVersion && (
        <p className="text-muted-foreground text-2xs [overflow-wrap:anywhere]">
          Agent <span className="font-mono">{state.agentVersion}</span>
        </p>
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
    ok: "bg-success/10 text-success-fg border-success-border",
    warn: "bg-warning/10 text-warning-fg border-warning-border",
    bad: "bg-destructive/10 text-destructive border-destructive/30",
    neutral: "bg-muted text-muted-foreground border-border",
  };
  return (
    <span
      className={`inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${classes[tone]}`}
    >
      <code className="min-w-0 truncate font-mono font-medium" title={appSlug}>
        {appSlug}
      </code>
      <span className="shrink-0 font-mono tabular-nums opacity-80">
        {ready}/{total} ready
      </span>
    </span>
  );
}

// ─── Metrics section: KPI bar + sparkline grid ────────────────────────
export function StatusMetricsCard({
  slug,
  selectedWindow,
  onWindowChange,
  range,
  rangeLoading,
  instant,
  instantLoading,
  error,
  refetch,
}: { slug: string } & ReturnType<typeof useClusterMetrics>) {
  return (
    <Panel
      icon={<BarChart3Icon className="size-4" />}
      title="Cluster saturation"
      description="Prometheus-sourced golden signals — current snapshot and historical trend."
      actions={<WindowSelector value={selectedWindow} onChange={onWindowChange} />}
      error={!instant && !range && !instantLoading && !rangeLoading ? error : null}
      onRetry={refetch}
    >
      <SaturationKPIBar instant={instant} loading={instantLoading} slug={slug} />
      <div className="mt-5">
        <SparklineGrid
          range={range}
          loading={rangeLoading}
          slug={slug}
          instantReason={instant?.reason ?? null}
        />
      </div>
    </Panel>
  );
}

// ─── Saturation KPI bar ──────────────────────────────────────────────
function SaturationKPIBar({
  instant,
  loading,
  slug,
}: {
  instant: PrometheusInstant | null;
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
        <div key={k.label} className="min-w-0 space-y-1">
          <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
            {k.label}
          </p>
          <p
            className={`font-mono text-2xl font-semibold tabular-nums ${TONE_COLORS[k.tone].text}`}
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
  range: PrometheusRange | null;
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
    <div className="border-warning-border bg-warning/5 flex items-start gap-3 rounded-md border p-3">
      <Icon className="text-warning mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 space-y-1">
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
    <div className="flex flex-wrap items-center gap-1">
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
    <div className="min-w-0 overflow-hidden rounded-md border">
      <div className="min-w-0 px-4 pt-4 pb-2">
        <span className="text-muted-foreground text-xs tracking-wide [overflow-wrap:anywhere] uppercase">
          {series.label}
        </span>
        <p className={`font-mono text-2xl font-semibold tabular-nums ${colors.text}`}>
          {fmtValue(series.current, series.unit)}
        </p>
      </div>
      <div>
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
          <div className="flex h-20 items-center justify-center">
            <span className="text-muted-foreground text-xs">
              {series.points.length === 0 ? "No data in window" : "Collecting data…"}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Workload health section ─────────────────────────────────────────
export function StatusWorkloadHealthCard({
  rows,
  loading,
  error,
  refetch,
}: ReturnType<typeof useClusterWorkloadHealth>) {
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
    <Panel
      span={6}
      icon={<ServerIcon className="size-4" />}
      title="Workload health"
      description="Per-Deployment readiness + 24h restart counts. Sorted most-broken first; polls every 30s."
      loading={loading && rows.length === 0}
      skeleton={<SkeletonRows />}
      error={rows.length === 0 ? error : null}
      onRetry={refetch}
      empty={
        rows.length === 0
          ? {
              icon: <ServerOffIcon className="size-5" />,
              title: "No deployment data",
              description: "Apiserver unreachable or no workloads running yet.",
            }
          : null
      }
    >
      <div className="space-y-4">
        {namespaces.map((ns) => (
          <div key={ns}>
            <p className="text-muted-foreground text-2xs mb-1.5 font-mono [overflow-wrap:anywhere]">
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
    </Panel>
  );
}

function WorkloadRowItem({ row }: { row: WorkloadRow }) {
  const deficit = row.desiredReplicas - row.readyReplicas;
  const readyTone =
    deficit === 0
      ? "text-success-fg"
      : deficit === row.desiredReplicas
        ? "text-destructive"
        : "text-warning-fg";

  const deployedAge = row.lastImageDeployedAt ? formatRelativeAge(row.lastImageDeployedAt) : null;

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" title={row.workloadName}>
        {row.workloadName}
      </code>
      <span className={`shrink-0 font-mono text-xs tabular-nums ${readyTone}`}>
        {row.readyReplicas} / {row.desiredReplicas}
      </span>
      {row.restartCount24h > 0 ? (
        <Badge
          variant="outline"
          className="border-warning-border text-warning-fg shrink-0 font-mono"
        >
          {row.restartCount24h} restart{row.restartCount24h === 1 ? "" : "s"}
        </Badge>
      ) : (
        <span className="text-muted-foreground/60 text-2xs shrink-0">no restarts</span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
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

export function StatusLiveHealthCard({
  pods,
  events,
  loading,
  error,
  refetch,
}: ReturnType<typeof useClusterHealth>) {
  // Aggregate pod counts by phase across all namespaces — the live
  // view answers "is the cluster green?" before "where is it red?".
  const phaseTotals = new Map<string, number>();
  for (const p of pods) {
    phaseTotals.set(p.phase, (phaseTotals.get(p.phase) ?? 0) + p.count);
  }
  const nothing = pods.length === 0 && events.length === 0;
  const phaseOrder = ["Running", "Pending", "Failed", "CrashLoopBackOff", "Succeeded", "Unknown"];
  const phasesSorted = Array.from(phaseTotals.entries()).sort(
    ([a], [b]) => phaseOrder.indexOf(a) - phaseOrder.indexOf(b)
  );

  return (
    <Panel
      span={6}
      icon={<ActivityIcon className="size-4" />}
      title="Live health"
      description="Pod-phase rollup + recent warning events from the cluster driver. Polls every 30s."
      loading={loading && nothing}
      skeleton={<Skeleton className="h-24 w-full" />}
      error={nothing ? error : null}
      onRetry={refetch}
      empty={
        nothing
          ? {
              icon: <ServerOffIcon className="size-5" />,
              title: "No health data",
              description: "The driver couldn't reach the apiserver.",
            }
          : null
      }
    >
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
    </Panel>
  );
}

function PodPhasePill({ phase, count }: { phase: string; count: number }) {
  const tone = POD_PHASE_TONE[phase] ?? "neutral";
  const classes: Record<Tone, string> = {
    ok: "bg-success/10 text-success-fg border-success-border",
    warn: "bg-warning/10 text-warning-fg border-warning-border",
    bad: "bg-destructive/10 text-destructive border-destructive/30",
    neutral: "bg-muted text-muted-foreground border-border",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${classes[tone]}`}
    >
      <span className="font-medium">{phase}</span>
      <span className="font-mono tabular-nums opacity-80">{count}</span>
    </span>
  );
}

function EventRow({ event }: { event: ClusterEvent }) {
  const isWarning = event.type === "Warning";
  const Icon = isWarning ? AlertTriangleIcon : InfoIcon;
  const iconClass = isWarning ? "text-warning" : "text-muted-foreground";
  const lastSeen = event.lastSeen ? formatRelativeAge(event.lastSeen) : "";

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <Icon className={`size-3.5 shrink-0 ${iconClass}`} />
      <code className="text-2xs shrink-0 font-mono">{event.reason}</code>
      <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
        <span className="font-mono">{event.involvedObject}</span>
        {event.message && (
          <>
            <span className="opacity-60"> · </span>
            <span>{event.message}</span>
          </>
        )}
      </span>
      {event.count > 1 && (
        <Badge variant="outline" className="text-2xs shrink-0 font-mono">
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
export function StatusRecentWorkflowsCard({
  runs,
  loading,
  error,
  refetch,
}: ReturnType<typeof useRecentClusterWorkflows>) {
  return (
    <Panel
      span={6}
      icon={<GitBranchIcon className="size-4" />}
      title="Recent workflow runs"
      description="Temporal runs targeting this cluster — most recent first."
      loading={loading && runs.length === 0}
      skeleton={<SkeletonRows />}
      error={runs.length === 0 ? error : null}
      onRetry={refetch}
      empty={
        runs.length === 0
          ? { icon: <GitBranchIcon className="size-5" />, title: "No workflow runs recorded yet" }
          : null
      }
    >
      <div>
        {runs.map((r) => (
          <WorkflowRunRow key={r.workflowId + r.runId} run={r} />
        ))}
      </div>
    </Panel>
  );
}

function WorkflowRunRow({ run }: { run: WorkflowRun }) {
  const { Icon, iconClass, label } = workflowStatusIcon(run.status);
  const startedAge = run.startedAt ? formatRelativeAge(run.startedAt) : "";
  const duration = fmtDuration(run.startedAt, run.closedAt);

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <Icon className={`size-4 shrink-0 ${iconClass}`} />
      <span className="min-w-0 flex-1 truncate font-medium" title={run.workflowType}>
        {prettyWorkflowType(run.workflowType)}
      </span>
      <Badge variant="outline" className="text-2xs shrink-0">
        {label}
      </Badge>
      {duration && (
        <span className="text-muted-foreground text-2xs inline-flex shrink-0 items-center gap-1 font-mono">
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
      iconClass: "text-success-fg",
      label: "Completed",
    };
  }
  if (norm === "RUNNING") {
    return {
      Icon: Loader2Icon,
      iconClass: "animate-spin text-warning-fg",
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
export function StatusLifecycleCard({
  entries,
  loading,
  error,
  refetch,
}: ReturnType<typeof useClusterLifecycleAudit>) {
  return (
    <Panel
      span={6}
      icon={<ClockIcon className="size-4" />}
      title="Lifecycle events"
      description="Mutations targeting this cluster — registered → managing → managed transitions, refreshes, decommissions."
      loading={loading && entries.length === 0}
      skeleton={<SkeletonRows />}
      error={entries.length === 0 ? error : null}
      onRetry={refetch}
      empty={
        entries.length === 0
          ? { icon: <ClockIcon className="size-5" />, title: "No lifecycle events recorded yet" }
          : null
      }
    >
      <div>
        {entries.map((e, i) => (
          <LifecycleRow key={`${e.timestamp}-${i}`} entry={e} />
        ))}
      </div>
    </Panel>
  );
}

function LifecycleRow({ entry }: { entry: AuditRow }) {
  const ts = entry.timestamp ? formatRelativeAge(entry.timestamp) : "";
  const dotClass = entry.success ? "text-success" : "text-destructive";

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <span className={`shrink-0 text-base leading-none ${dotClass}`}>●</span>
      <span className="min-w-0 flex-1 truncate font-medium" title={entry.operation}>
        {prettyOperation(entry.operation)}
      </span>
      {entry.actor && (
        <span className="text-muted-foreground text-2xs min-w-0 truncate">
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
