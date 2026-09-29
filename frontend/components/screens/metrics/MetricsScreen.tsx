"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  CheckCircle2Icon,
  ClockIcon,
  ExternalLinkIcon,
  FlameIcon,
  GaugeIcon,
} from "lucide-react";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import type { ListStateController } from "@/components/list/use-list-state";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MiniBar, RadialGauge, Sparkline } from "@/components/viz";
import type {
  AstroliftAppHealthSummary,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import { METRICS_APPS_SELECT } from "./metrics-apps-list";
import type { useMetrics } from "./use-metrics";

export type MetricsScreenProps = Omit<ReturnType<typeof useMetrics>, "list">;

const STATUS_DOT: Record<DeploymentStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  pending_approval: "warn",
  pending: "warn",
  deploying: "pending",
  redeploying: "pending",
  running: "ok",
  failed: "error",
  superseded: "muted",
  rolled_back: "muted",
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function formatPercent(rate: number): string {
  if (rate < 0) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

// Per-day success rate for the trend line — only days that saw a rollout
// (the rate is undefined on a zero-deploy day), oldest → newest.
function successRateSeries(succeeded: number[], failed: number[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < succeeded.length; i++) {
    const n = succeeded[i] + failed[i];
    if (n > 0) out.push(succeeded[i] / n);
  }
  return out;
}

// Duration trend — drop days with no rollout (null mean) so the line tracks
// real durations instead of dipping to zero on quiet days.
function durationTrendSeries(daily: (number | null)[]): number[] {
  return daily.filter((d): d is number => d != null);
}

// Total terminal rollouts per day (succeeded + failed) for the volume bars.
function rolloutsPerDaySeries(succeeded: number[], failed: number[]): number[] {
  return succeeded.map((s, i) => s + (failed[i] ?? 0));
}

function MetricCard({
  icon,
  label,
  value,
  hint,
  trend,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  trend?: number[];
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
        <span className="text-muted-foreground">{icon}</span>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-3">
          <div className="text-2xl font-semibold tabular-nums">{value}</div>
          {trend && trend.length > 1 && (
            <Sparkline
              data={trend}
              variant="area"
              className="text-chart-1 shrink-0"
              ariaLabel={`${label} trend`}
            />
          )}
        </div>
        {hint && <p className="text-muted-foreground mt-1 text-xs">{hint}</p>}
      </CardContent>
    </Card>
  );
}

// Success rate rendered as a gauge — the one KPI that's a bounded ratio.
// Coloured by the same thresholds the fleet uses: ≥90% healthy, ≥70% watch.
function SuccessRateCard({
  rate,
  label,
  hint,
  trend,
}: {
  rate: number;
  label: string;
  hint?: string;
  trend?: number[];
}) {
  const known = rate >= 0;
  const tone = !known
    ? "text-muted-foreground"
    : rate >= 0.9
      ? "text-success-fg"
      : rate >= 0.7
        ? "text-warning-fg"
        : "text-danger-fg";
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">
          Deployment success rate
        </CardTitle>
        <CheckCircle2Icon className="text-muted-foreground size-4" />
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-3">
          <RadialGauge
            value={known ? rate : 0}
            size={56}
            label={known ? label : "—"}
            className={tone}
            ariaLabel="Deployment success rate"
          />
          {trend && trend.length > 1 && (
            <Sparkline
              data={trend}
              variant="area"
              className={cn("ml-auto shrink-0", tone)}
              ariaLabel="Deployment success-rate trend"
            />
          )}
        </div>
        {hint && <p className="text-muted-foreground mt-2 min-w-0 text-xs">{hint}</p>}
      </CardContent>
    </Card>
  );
}

/**
 * Platform health rollups: deployment KPIs, outcomes, then per-app status
 * as the page's one embedded list (search, filters, sort, numbered pages,
 * run over the summary in hand: the field returns every app at once).
 */
export function MetricsScreen({
  windowDays,
  metrics,
  metricsLoading,
  apps,
  healthLoading,
  list,
}: MetricsScreenProps & { list: ListStateController }) {
  const fmt = useFormatters();
  const { state } = list;
  const page = selectRows(
    apps,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    METRICS_APPS_SELECT
  );

  const columns: Column<AstroliftAppHealthSummary>[] = [
    {
      id: "app",
      header: "App",
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (a) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot
            status={a.latestDeploymentStatus ? STATUS_DOT[a.latestDeploymentStatus] : "muted"}
            className="mt-1.5 shrink-0"
          />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={a.appName}>
              {a.appName}
            </span>
            <span className="text-muted-foreground block truncate font-mono text-xs">
              {a.appSlug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "envs",
      header: "Envs",
      sortKey: "envs",
      cellClassName: "font-mono text-xs",
      cell: (a) => a.environmentCount,
    },
    {
      id: "latest",
      header: "Latest deploy",
      cell: (a) => (
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          {a.latestDeploymentStatus ? (
            <Badge variant="secondary" className="capitalize">
              {a.latestDeploymentStatus.replace(/_/g, " ")}
            </Badge>
          ) : (
            <span className="text-muted-foreground text-xs">never</span>
          )}
          {a.hasRecentFailure && (
            <Badge variant="outline" className="border-danger-border text-danger-fg gap-1">
              <FlameIcon className="size-3" />
              recent failure
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "image",
      header: "Image",
      cellClassName: "max-w-56",
      cell: (a) => (
        <span className="block truncate font-mono text-xs" title={a.latestImageTag || undefined}>
          {a.latestImageTag || "—"}
        </span>
      ),
    },
    {
      id: "deployed",
      header: "Last deployed",
      sortKey: "deployed",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (a) => (a.lastDeployedAt ? fmt.formatDateTime(a.lastDeployedAt) : "—"),
    },
  ];

  const rateTrend = metrics ? successRateSeries(metrics.dailySucceeded, metrics.dailyFailed) : [];
  const durationTrend = metrics ? durationTrendSeries(metrics.dailyMeanDurationSeconds) : [];
  const rolloutsTrend = metrics
    ? rolloutsPerDaySeries(metrics.dailySucceeded, metrics.dailyFailed)
    : [];

  const successRateLabel = metrics ? formatPercent(metrics.successRate) : "—";
  const successRateHint = metrics
    ? metrics.total === 0
      ? `No rollouts in the last ${windowDays} days`
      : `${metrics.succeeded}/${metrics.total} succeeded over ${windowDays} days`
    : undefined;

  return (
    <PageShell
      title="Metrics"
      description="Platform health rollups: deployment success rate, recent rollout durations, per-app status. Raw Prometheus exposition is linked at the bottom."
    >
      {/* Top KPI row */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metricsLoading && !metrics ? (
          [...Array(4)].map((_, i) => <Skeleton key={i} className="h-28 w-full" />)
        ) : (
          <>
            <SuccessRateCard
              rate={metrics?.successRate ?? -1}
              label={successRateLabel}
              hint={successRateHint}
              trend={rateTrend}
            />
            <MetricCard
              icon={<ActivityIcon className="size-4" />}
              label="In flight"
              value={metrics ? String(metrics.inFlight) : "—"}
              hint="Pending approval, pending, deploying, or redeploying"
            />
            <MetricCard
              icon={<ClockIcon className="size-4" />}
              label="Mean rollout"
              value={formatDuration(metrics?.meanDurationSeconds ?? null)}
              hint="Average across terminal-state deploys in window"
              trend={durationTrend}
            />
            <MetricCard
              icon={<GaugeIcon className="size-4" />}
              label="p95 rollout"
              value={formatDuration(metrics?.p95DurationSeconds ?? null)}
              hint="Slow tail — anything over a couple of minutes warrants a look"
            />
          </>
        )}
      </div>

      {/* Outcome counts */}
      {metrics && metrics.total > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Outcomes ({windowDays}d)</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary" className="gap-1">
                <CheckCircle2Icon className="text-success-fg size-3" />
                succeeded {metrics.succeeded}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <FlameIcon className="text-danger-fg size-3" />
                failed {metrics.failed}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <AlertTriangleIcon className="text-warning-fg size-3" />
                rolled back {metrics.rolledBack}
              </Badge>
              <Badge variant="outline">in flight {metrics.inFlight}</Badge>
              <Badge variant="outline">total {metrics.total}</Badge>
            </div>
            {rolloutsTrend.some((n) => n > 0) && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-2xs tracking-wide uppercase">
                  {windowDays}-day rollouts
                </span>
                <MiniBar
                  data={rolloutsTrend}
                  width={180}
                  height={32}
                  className="text-chart-1"
                  ariaLabel="Rollouts per day"
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Per-app health: the page's one list */}
      <section className="flex min-w-0 flex-col gap-3" aria-label="Apps">
        <h2 className="text-base font-semibold">Apps</h2>
        <ListPage<AstroliftAppHealthSummary>
          embedded
          list={list}
          label="Apps"
          columns={columns}
          rows={page.rows}
          getRowId={(a) => a.appSlug}
          rowHref={(a) =>
            `/${a.primitiveKind === "agent" ? "agents" : "apps"}/${encodeURIComponent(a.appSlug)}`
          }
          loading={healthLoading && apps.length === 0}
          totalCount={page.totalCount}
          empty={{
            icon: <BarChart3Icon className="size-5" />,
            title: "No apps yet",
            description: "Register an app on the Apps page; metrics will populate once it deploys.",
            actionHref: "/apps",
            actionLabel: "Open apps",
          }}
        />
      </section>

      {/* Power-user links */}
      <div className="text-muted-foreground flex flex-wrap gap-4 border-t pt-4 text-xs">
        <a
          className="inline-flex items-center gap-1 hover:underline"
          href="/app/metrics/"
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLinkIcon className="size-3" />
          Raw Prometheus exposition
        </a>
        <a
          className="inline-flex items-center gap-1 hover:underline"
          href="http://localhost:8233"
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLinkIcon className="size-3" />
          Temporal UI (workflows)
        </a>
        <span className="text-muted-foreground/70">
          Grafana embeds land in v2 — wire a prometheus + grafana docker profile and these cards
          become live charts.
        </span>
      </div>
    </PageShell>
  );
}
