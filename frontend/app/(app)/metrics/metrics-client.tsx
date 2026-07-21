"use client";

import { useQuery } from "@apollo/client/react";
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

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MiniBar, RadialGauge, Sparkline } from "@/components/viz";
import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}
interface HealthResp {
  astroliftAppHealthSummary: AstroliftAppHealthSummary[];
}

const STATUS_DOT: Record<
  DeploymentStatus,
  "ok" | "warn" | "error" | "muted" | "pending"
> = {
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
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
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
        {hint && (
          <p className="text-muted-foreground mt-1 text-xs">{hint}</p>
        )}
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
        <CardTitle className="text-sm font-medium text-muted-foreground">
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

export function MetricsClient() {
  const fmt = useFormatters();
  const [windowDays] = React.useState(30);

  const { data: metricsData, loading: metricsLoading } = useQuery<MetricsResp>(
    GET_DEPLOYMENT_METRICS,
    { variables: { windowDays }, pollInterval: 30000 },
  );
  const { data: healthData, loading: healthLoading } = useQuery<HealthResp>(
    LIST_APP_HEALTH_SUMMARY,
    { pollInterval: 30000 },
  );

  const metrics = metricsData?.astroliftDeploymentMetrics;
  const apps = healthData?.astroliftAppHealthSummary ?? [];

  const rateTrend = metrics
    ? successRateSeries(metrics.dailySucceeded, metrics.dailyFailed)
    : [];
  const durationTrend = metrics
    ? durationTrendSeries(metrics.dailyMeanDurationSeconds)
    : [];
  const rolloutsTrend = metrics
    ? rolloutsPerDaySeries(metrics.dailySucceeded, metrics.dailyFailed)
    : [];

  const successRateLabel = metrics
    ? formatPercent(metrics.successRate)
    : "—";
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
          [...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))
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
                <CheckCircle2Icon className="size-3 text-success-fg" />
                succeeded {metrics.succeeded}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <FlameIcon className="size-3 text-danger-fg" />
                failed {metrics.failed}
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <AlertTriangleIcon className="size-3 text-warning-fg" />
                rolled back {metrics.rolledBack}
              </Badge>
              <Badge variant="outline">in flight {metrics.inFlight}</Badge>
              <Badge variant="outline">total {metrics.total}</Badge>
            </div>
            {rolloutsTrend.some((n) => n > 0) && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground text-2xs uppercase tracking-wide">
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

      {/* Per-app health table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Apps</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {healthLoading && apps.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : apps.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BarChart3Icon className="size-5" />}
                title="No apps yet"
                description="Register an app on the Apps page; metrics will populate once it deploys."
                actionHref="/apps"
                actionLabel="Open apps"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>App</TableHead>
                  <TableHead>Envs</TableHead>
                  <TableHead>Latest deploy</TableHead>
                  <TableHead>Image</TableHead>
                  <TableHead>Last deployed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {apps.map((a) => (
                  <TableRow key={a.appSlug}>
                    <TableCell className="w-8">
                      <StatusDot
                        status={
                          a.latestDeploymentStatus
                            ? STATUS_DOT[a.latestDeploymentStatus]
                            : "muted"
                        }
                      />
                    </TableCell>
                    <TableCell>
                      <div className="font-medium">{a.appName}</div>
                      <div className="text-muted-foreground font-mono text-xs">
                        {a.appSlug}
                      </div>
                    </TableCell>
                    <TableCell>{a.environmentCount}</TableCell>
                    <TableCell>
                      {a.latestDeploymentStatus ? (
                        <Badge
                          variant="secondary"
                          className="capitalize"
                        >
                          {a.latestDeploymentStatus.replace(/_/g, " ")}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">
                          never
                        </span>
                      )}
                      {a.hasRecentFailure && (
                        <Badge
                          variant="outline"
                          className="ml-2 gap-1 border-danger-border text-danger-fg"
                        >
                          <FlameIcon className="size-3" />
                          recent failure
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {a.latestImageTag || "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {a.lastDeployedAt
                        ? fmt.formatDateTime(a.lastDeployedAt)
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

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
          Grafana embeds land in v2 — wire a prometheus + grafana docker
          profile and these cards become live charts.
        </span>
      </div>
    </PageShell>
  );
}
