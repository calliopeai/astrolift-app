"use client";

/**
 * Cluster > Status (#68, #808): the keep-alive connection card, then the
 * driver-dependent cards (saturation, workload health, live health, recent
 * workflows) or an offline stand-in for each, then the lifecycle timeline.
 *
 * Every card here is a pure view over its hook in this folder. The
 * driver-dependent cards arrive as slots so the container can leave them
 * unmounted (their queries never fire) while the cluster is offline. The
 * tab is an overview (list rule 3): workloads, workflow runs and lifecycle
 * events are summaries of their top rows, each with "View all" to the
 * Health or Activity tab, which hold the full list or feed.
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
import { useFormatter, useTranslations } from "next-intl";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { ListSummary } from "@/components/list/ListSummary";
import { Panel, PanelGrid, type PanelSpan } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  isClusterLive,
  type ClusterLiveState,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { useClusterHeartbeat } from "@/lib/i18n/cluster-heartbeat";
import { useClusterActivity } from "@/lib/i18n/cluster-activity";

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
  const t = useTranslations("clusterConnection");
  const state = liveState.state;
  const status: HeartbeatStatus = typeof state?.status === "string" ? state.status : "";
  const age = state?.heartbeatAgeSeconds ?? null;
  const live = isClusterLive(status);

  return (
    <PanelGrid>
      <StatusLiveStateCard slug={slug} {...liveState} />
      {!state || liveState.loading || liveState.error ? (
        liveState.loading ? (
          <>
            {[t("saturation"), t("workloadHealth"), t("liveHealth"), t("recentWorkflows")].map(
              (title, index) => (
                <Panel
                  key={title}
                  title={title}
                  span={index === 0 ? 12 : 6}
                  loading
                  skeleton={<Skeleton className="h-24 w-full" />}
                />
              )
            )}
          </>
        ) : null
      ) : live ? (
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
            title={t("saturation")}
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<ServerIcon className="size-4" />}
            title={t("workloadHealth")}
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<ActivityIcon className="size-4" />}
            title={t("liveHealth")}
            status={status}
            age={age}
            slug={slug}
          />
          <OfflineCard
            icon={<GitBranchIcon className="size-4" />}
            title={t("recentWorkflows")}
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
  const t = useTranslations("clusterConnection");
  const p = useClusterHeartbeat(status, age);
  return (
    <Panel
      span={span}
      icon={icon}
      title={title}
      actions={
        <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
          {status === "never_seen" ? (
            <ServerOffIcon className="size-3.5" />
          ) : status === "offline" ? (
            <WifiOffIcon className="size-3.5" />
          ) : (
            <InfoIcon className="size-3.5" />
          )}
          {p.label}
        </span>
      }
    >
      <div className="border-border/70 text-muted-foreground flex items-start gap-3 rounded-md border border-dashed px-3 py-4 text-sm">
        {status === "offline" || status === "never_seen" ? (
          <WifiOffIcon className="text-muted-foreground/70 mt-0.5 size-4 shrink-0" />
        ) : (
          <InfoIcon className="text-muted-foreground/70 mt-0.5 size-4 shrink-0" />
        )}
        <div className="min-w-0 space-y-1">
          <p>{p.message}</p>
          <Link
            href={`/clusters/${slug}/settings`}
            className="text-primary inline-block text-xs underline-offset-4 hover:underline"
          >
            {t("checkSettings")}
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
  const t = useTranslations("clusterConnection");
  const sourceT = useTranslations("clusterSettings.source");
  const status: HeartbeatStatus = typeof state?.status === "string" ? state.status : "";
  const p = useClusterHeartbeat(status, state?.heartbeatAgeSeconds ?? null);
  const age = p.age;
  const live = isClusterLive(status);

  if (!state || loading || error) {
    return (
      <Panel
        icon={<ActivityIcon className="size-4" />}
        title={t("title")}
        description={t("help")}
        loading={loading && !state}
        skeleton={<Skeleton className="h-16 w-full" />}
        error={!state ? (error ?? (loading ? null : t("unavailable"))) : null}
        onRetry={refetch}
        failure={
          state && error
            ? {
                title: t("unavailable"),
                reason: error,
                action: (
                  <Button variant="outline" size="sm" onClick={refetch}>
                    {sourceT("retry")}
                  </Button>
                ),
              }
            : null
        }
      >
        {state && (
          <div className="space-y-3">
            {loading && <p className="text-muted-foreground text-sm">{t("unconfirmed")}</p>}
            <p className="text-muted-foreground text-sm">{t("cached")}</p>
            <LiveSnapshotGrid state={state} age={age} />
          </div>
        )}
      </Panel>
    );
  }

  return (
    <Panel
      icon={<ActivityIcon className="size-4" />}
      title={t("title")}
      description={t("help")}
      actions={
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${p.pill}`}
        >
          {live ? (
            <CircleDotIcon className="size-3" />
          ) : status === "never_seen" ? (
            <ServerOffIcon className="size-3" />
          ) : status === "offline" ? (
            <WifiOffIcon className="size-3" />
          ) : (
            <InfoIcon className="size-3" />
          )}
          {p.label}
        </span>
      }
    >
      {status === "never_seen" ? (
        <div className="border-border/70 flex items-start gap-3 rounded-md border border-dashed p-3">
          <ServerOffIcon className="text-muted-foreground mt-0.5 size-5 shrink-0" />
          <div className="min-w-0 space-y-1 text-sm">
            <p className="font-medium">{t("noAgentTitle")}</p>
            <p className="text-muted-foreground">{t("noAgentHelp")}</p>
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary mt-1 inline-block text-xs underline-offset-4 hover:underline"
            >
              {t("setUp")}
            </Link>
          </div>
        </div>
      ) : !live ? (
        <div
          className={`flex items-start gap-3 rounded-md border p-3 ${status === "offline" ? "border-destructive/30 bg-destructive/5" : "border-border bg-muted/30"}`}
        >
          {status === "offline" ? (
            <WifiOffIcon className="text-destructive mt-0.5 size-5 shrink-0" />
          ) : (
            <InfoIcon className="text-muted-foreground mt-0.5 size-5 shrink-0" />
          )}
          <div className="min-w-0 space-y-1 text-sm">
            <p className="font-medium">{t(status === "offline" ? "disconnected" : "unknown")}</p>
            <p className="text-muted-foreground">
              {p.message} {t("recoveryHelp")}
            </p>
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary mt-1 inline-block text-xs underline-offset-4 hover:underline"
            >
              {t("checkSettings")}
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
  const t = useTranslations("clusterConnection");
  const count = (value: unknown): value is number =>
    typeof value === "number" && Number.isInteger(value) && value >= 0;
  const percentage = (value: unknown) =>
    typeof value === "number" &&
    Number.isFinite(value) &&
    Number.isFinite(value * 100) &&
    value >= 0
      ? `${(value * 100).toFixed(0)}%`
      : "—";
  // Node readiness — "N/M ready" when the agent reports the ready count
  // (#112), falling back to the bare total for agents that predate it.
  const nodeValue: React.ReactNode =
    count(state.nodeReadyCount) &&
    count(state.nodeCount) &&
    state.nodeReadyCount <= state.nodeCount ? (
      <>
        {state.nodeReadyCount}/{state.nodeCount}
        <span className="text-muted-foreground ml-1 text-xs font-normal">{t("ready")}</span>
      </>
    ) : count(state.nodeCount) && state.nodeReadyCount == null ? (
      String(state.nodeCount)
    ) : (
      "—"
    );

  const tiles: { label: string; value: React.ReactNode }[] = [
    { label: t("lastHeartbeat"), value: age ?? "—" },
    { label: t("nodes"), value: nodeValue },
    {
      label: t("pods"),
      value: count(state.podTotal) ? String(state.podTotal) : "—",
    },
    {
      label: "CPU",
      value: percentage(state.cpuUtilization),
    },
    {
      label: t("memory"),
      value: percentage(state.memoryUtilization),
    },
  ];

  // JSON reports are independent of the connection token. Keep malformed
  // reports unknown; cached snapshots are explicitly labeled by the caller.
  const knownReadiness =
    state.appReadiness !== null &&
    typeof state.appReadiness === "object" &&
    !Array.isArray(state.appReadiness);
  const appEntries = Object.entries(knownReadiness ? state.appReadiness : {}).sort(([a], [b]) =>
    a.localeCompare(b)
  );
  const knownIngress =
    Array.isArray(state.ingressIps) && state.ingressIps.every((ip) => typeof ip === "string");
  const ingressIps = knownIngress ? state.ingressIps : [];
  const knownVersion = typeof state.agentVersion === "string";

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
      {(!knownReadiness || appEntries.length > 0) && (
        <div className="space-y-1.5">
          <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
            {t("appReadiness")}
          </p>
          <div className="flex flex-wrap gap-2">
            {!knownReadiness && (
              <span className="text-muted-foreground text-xs">{t("unknown")}</span>
            )}
            {appEntries.map(([appSlug, r]) => (
              <AppReadinessPill key={appSlug} appSlug={appSlug} value={r} />
            ))}
          </div>
        </div>
      )}
      {(!knownIngress || ingressIps.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">{t("ingress")}:</span>
          {!knownIngress && <span>{t("unknown")}</span>}
          {ingressIps.map((ip) => (
            <code
              key={ip}
              className="bg-muted text-2xs max-w-full rounded px-1.5 py-0.5 font-mono [overflow-wrap:anywhere]"
            >
              {ip}
            </code>
          ))}
        </div>
      )}
      {(!knownVersion || state.agentVersion) && (
        <p className="text-muted-foreground text-2xs [overflow-wrap:anywhere]">
          {t("agent")}{" "}
          <span className="font-mono">{knownVersion ? state.agentVersion : t("unknown")}</span>
        </p>
      )}
    </div>
  );
}

// Per-app pod readiness pill (#112). Tone tracks the ready/total ratio —
// green when fully ready, red when nothing is ready, amber in between.
// Mirrors the PodPhasePill shape so the live cards read consistently.
function AppReadinessPill({ appSlug, value }: { appSlug: string; value: unknown }) {
  const t = useTranslations("clusterConnection");
  const row =
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const ready = row.ready;
  const total = row.total;
  const known =
    typeof ready === "number" &&
    typeof total === "number" &&
    Number.isInteger(ready) &&
    Number.isInteger(total) &&
    ready >= 0 &&
    total >= ready;
  const tone: Tone =
    !known || total === 0 ? "neutral" : ready === total ? "ok" : ready === 0 ? "bad" : "warn";
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
        {known ? (
          <>
            {ready}/{total} {t("ready")}
          </>
        ) : (
          t("unknown")
        )}
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
  slug,
  rows,
  loading,
  error,
  refetch,
}: ReturnType<typeof useClusterWorkloadHealth> & { slug: string }) {
  const t = useTranslations("clusterHealth");
  // Most broken first; the full list is the Health tab's.
  const sorted = [...rows].sort((a, b) => {
    const deficitA = a.desiredReplicas - a.readyReplicas;
    const deficitB = b.desiredReplicas - b.readyReplicas;
    if (deficitA !== deficitB) return deficitB - deficitA;
    if (a.restartCount24h !== b.restartCount24h) return b.restartCount24h - a.restartCount24h;
    return a.workloadName.localeCompare(b.workloadName);
  });

  return (
    <ListSummary<WorkloadRow>
      span={6}
      icon={<ServerIcon className="size-4" />}
      title={t("workloadTitle")}
      description={
        <>
          {t("workloadHelp")}
          {rows.length > 0 && (loading || error) && (
            <CachedHealthNotice loading={loading} error={error} refetch={refetch} />
          )}
        </>
      }
      count={rows.length}
      rows={sorted}
      keyOf={(r) => `${r.namespace}/${r.workloadName}`}
      renderRow={(r) => <WorkloadRowItem row={r} />}
      viewAllHref={`/clusters/${slug}/health`}
      loading={loading}
      error={error}
      onRetry={refetch}
      empty={{
        icon: <ServerOffIcon className="size-5" />,
        title: t("noDeployments"),
        description: t("noDeploymentsHelp"),
      }}
    />
  );
}

function WorkloadRowItem({ row }: { row: WorkloadRow }) {
  const t = useTranslations("clusterHealth");
  const format = useFormatter();
  const activity = useClusterActivity();
  const deficit = row.desiredReplicas - row.readyReplicas;
  const knownReadiness =
    observedCount(row.desiredReplicas) &&
    observedCount(row.readyReplicas) &&
    row.readyReplicas <= row.desiredReplicas;
  const readyTone =
    !knownReadiness || row.desiredReplicas === 0
      ? "text-muted-foreground"
      : deficit === 0
        ? "text-success-fg"
        : deficit === row.desiredReplicas
          ? "text-destructive"
          : "text-warning-fg";

  const deployedAge = activity.relative(row.lastImageDeployedAt);

  return (
    <div className="flex min-w-0 items-center gap-3 text-sm">
      <code
        className="min-w-0 flex-1 truncate font-mono text-xs"
        title={`${row.namespace}/${row.workloadName}`}
      >
        {row.workloadName}
      </code>
      <span className={`shrink-0 font-mono text-xs tabular-nums ${readyTone}`}>
        {knownReadiness
          ? `${format.number(row.readyReplicas)} / ${format.number(row.desiredReplicas)}`
          : t("unknown")}
      </span>
      {!observedCount(row.restartCount24h) ? (
        <span className="text-muted-foreground text-2xs shrink-0">{t("unknown")}</span>
      ) : row.restartCount24h > 0 ? (
        <Badge
          variant="outline"
          className="border-warning-border text-warning-fg shrink-0 font-mono"
        >
          {t("restarts", { count: row.restartCount24h })}
        </Badge>
      ) : (
        <span className="text-muted-foreground/60 text-2xs shrink-0">{t("noRestarts")}</span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
        {deployedAge}
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
const POD_PHASE_LABEL = {
  Running: "running",
  Succeeded: "succeeded",
  Pending: "pending",
  Failed: "failed",
  CrashLoopBackOff: "crashLoop",
  Unknown: "phaseUnknown",
} as const;

function observedCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function CachedHealthNotice({
  loading,
  error,
  refetch,
}: {
  loading: boolean;
  error: string | null;
  refetch: () => void;
}) {
  const t = useTranslations("clusterHealth");
  return (
    <span role={error ? "alert" : "status"} className="mt-2 block space-y-1">
      <span className="block">{t(error ? "cachedFailed" : "cachedPending")}</span>
      {error && (
        <>
          <code className="block font-mono [overflow-wrap:anywhere]">{error}</code>
          <Button size="sm" variant="outline" disabled={loading} onClick={refetch}>
            {t("retry")}
          </Button>
        </>
      )}
    </span>
  );
}

export function StatusLiveHealthCard({
  pods,
  events,
  loading,
  error,
  refetch,
}: Omit<ReturnType<typeof useClusterHealth>, "moreEvents">) {
  const t = useTranslations("clusterHealth");
  // Aggregate pod counts by phase across all namespaces — the live
  // view answers "is the cluster green?" before "where is it red?".
  const phaseTotals = new Map<string, number | null>();
  for (const p of pods) {
    const previous = phaseTotals.has(p.phase) ? phaseTotals.get(p.phase)! : 0;
    const total = previous === null || !observedCount(p.count) ? null : previous + p.count;
    phaseTotals.set(p.phase, observedCount(total) ? total : null);
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
      title={t("liveTitle")}
      description={
        <>
          {t("liveHelp")}
          {!nothing && (loading || error) && (
            <CachedHealthNotice loading={loading} error={error} refetch={refetch} />
          )}
        </>
      }
      loading={loading && nothing}
      skeleton={<Skeleton className="h-24 w-full" />}
      error={nothing ? error : null}
      onRetry={refetch}
      empty={
        nothing
          ? {
              icon: <ServerOffIcon className="size-5" />,
              title: t("noHealth"),
              description: t("noHealthHelp"),
            }
          : null
      }
    >
      <div className="space-y-4">
        <div>
          <p className="text-muted-foreground text-2xs mb-2 font-medium tracking-wider uppercase">
            {t("podPhases")}
          </p>
          {phasesSorted.length === 0 ? (
            <p className="text-muted-foreground text-xs">{t("noPods")}</p>
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
            {t("recentEvents")}
          </p>
          {events.length === 0 ? (
            <p className="text-muted-foreground text-xs">{t("noEvents")}</p>
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

function PodPhasePill({ phase, count }: { phase: string; count: number | null }) {
  const t = useTranslations("clusterHealth");
  const format = useFormatter();
  const known = Object.hasOwn(POD_PHASE_TONE, phase);
  const tone = count === null || !known ? "neutral" : POD_PHASE_TONE[phase];
  const label = Object.hasOwn(POD_PHASE_LABEL, phase)
    ? t(POD_PHASE_LABEL[phase as keyof typeof POD_PHASE_LABEL])
    : phase || t("unknown");
  const classes: Record<Tone, string> = {
    ok: "bg-success/10 text-success-fg border-success-border",
    warn: "bg-warning/10 text-warning-fg border-warning-border",
    bad: "bg-destructive/10 text-destructive border-destructive/30",
    neutral: "bg-muted text-muted-foreground border-border",
  };
  return (
    <span
      title={phase}
      className={`inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${classes[tone]}`}
    >
      <span className="min-w-0 truncate font-medium">{label}</span>
      <span className="shrink-0 font-mono tabular-nums opacity-80">
        {count === null ? t("unknown") : format.number(count)}
      </span>
    </span>
  );
}

function EventRow({ event }: { event: ClusterEvent }) {
  const activity = useClusterActivity();
  const isWarning = event.type === "Warning";
  const Icon = isWarning ? AlertTriangleIcon : InfoIcon;
  const iconClass = isWarning ? "text-warning" : "text-muted-foreground";
  const lastSeen = activity.relative(event.lastSeen);

  return (
    <div className="border-border/50 flex min-w-0 items-center gap-3 border-b py-2 text-sm last:border-0">
      <Icon className={`size-3.5 shrink-0 ${iconClass}`} />
      <code className="text-2xs max-w-1/3 shrink-0 truncate font-mono" title={event.reason}>
        {event.reason}
      </code>
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
  slug,
  runs,
  loading,
  error,
  refetch,
}: Omit<ReturnType<typeof useRecentClusterWorkflows>, "more"> & { slug: string }) {
  const { t } = useClusterActivity();
  return (
    <ListSummary<WorkflowRun>
      span={6}
      icon={<GitBranchIcon className="size-4" />}
      title={t("workflowTitle")}
      description={t("workflowSummaryHelp")}
      rows={runs}
      keyOf={(r) => r.workflowId + r.runId}
      renderRow={(r) => <WorkflowRunRow run={r} />}
      viewAllHref={`/clusters/${slug}/activity`}
      loading={loading}
      error={error}
      onRetry={refetch}
      empty={{ icon: <GitBranchIcon className="size-5" />, title: t("noRuns") }}
    />
  );
}

function WorkflowRunRow({ run }: { run: WorkflowRun }) {
  const activity = useClusterActivity();
  const { Icon, iconClass } = workflowStatusIcon(run.status);
  const status = activity.status(run.status);
  const startedAge = activity.relative(run.startedAt);
  const duration = activity.duration(run.startedAt, run.closedAt, true);

  return (
    <div className="flex min-w-0 items-center gap-3 text-sm">
      <Icon className={`size-4 shrink-0 ${iconClass}`} />
      <span className="min-w-0 flex-1 truncate font-medium" title={run.workflowType}>
        {activity.operation(run.workflowType)}
      </span>
      <Badge variant="outline" className="text-2xs max-w-40 shrink-0" title={status.token}>
        <span className="min-w-0 truncate">{status.label}</span>
      </Badge>
      {duration && (
        <span className="text-muted-foreground text-2xs inline-flex shrink-0 items-center gap-1 font-mono">
          <ClockIcon className="size-3" />
          {duration}
        </span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
        {startedAge}
      </span>
    </div>
  );
}

function workflowStatusIcon(status: string): {
  Icon: React.ComponentType<{ className?: string }>;
  iconClass: string;
} {
  if (status === "COMPLETED") {
    return {
      Icon: CheckCircle2Icon,
      iconClass: "text-success-fg",
    };
  }
  if (status === "RUNNING") {
    return {
      Icon: Loader2Icon,
      iconClass: "animate-spin text-warning-fg",
    };
  }
  if (status === "FAILED" || status === "TIMED_OUT" || status === "TERMINATED") {
    return {
      Icon: XCircleIcon,
      iconClass: "text-destructive",
    };
  }
  return {
    Icon: CircleDotIcon,
    iconClass: "text-muted-foreground",
  };
}

// ─── Lifecycle timeline section ──────────────────────────────────────
export function StatusLifecycleCard({
  slug,
  entries,
  loading,
  error,
  refetch,
}: Omit<ReturnType<typeof useClusterLifecycleAudit>, "more"> & { slug: string }) {
  const { t } = useClusterActivity();
  return (
    <ListSummary<AuditRow>
      span={6}
      icon={<ClockIcon className="size-4" />}
      title={t("lifecycleLabel")}
      description={t("lifecycleDescription")}
      rows={entries}
      keyOf={(e) => `${e.timestamp}-${e.operation}`}
      renderRow={(e) => <LifecycleRow entry={e} />}
      viewAllHref={`/clusters/${slug}/activity`}
      loading={loading}
      error={error}
      onRetry={refetch}
      empty={{ icon: <ClockIcon className="size-5" />, title: t("noEvents") }}
    />
  );
}

function LifecycleRow({ entry }: { entry: AuditRow }) {
  const activity = useClusterActivity();
  const ts = activity.relative(entry.timestamp);
  const dotClass = entry.success ? "text-success" : "text-destructive";

  return (
    <div className="flex min-w-0 items-center gap-3 text-sm">
      <span className={`shrink-0 text-base leading-none ${dotClass}`}>●</span>
      <span className="min-w-0 flex-1 truncate font-medium" title={entry.operation}>
        {activity.operation(entry.operation)}
      </span>
      {entry.actor && (
        <span className="text-muted-foreground text-2xs min-w-0 truncate">
          {activity.t.rich("actor", {
            name: entry.actor,
            actor: (chunks) => <span className="font-mono">{chunks}</span>,
          })}
        </span>
      )}
      {!entry.success && entry.errors.length > 0 && (
        <span className="text-destructive text-2xs max-w-2/5 truncate" title={entry.errors[0]}>
          {entry.errors[0]}
        </span>
      )}
      <span className="text-muted-foreground text-2xs w-20 shrink-0 text-right font-mono">
        {ts}
      </span>
    </div>
  );
}
