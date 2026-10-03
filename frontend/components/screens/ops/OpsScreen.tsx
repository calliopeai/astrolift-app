"use client";

import { QueryError } from "@/components/QueryError";

import {
  ActivityIcon,
  BellIcon,
  CheckCircle2Icon,
  ClockIcon,
  ExternalLinkIcon,
  LayersIcon,
  RocketIcon,
  ScrollTextIcon,
  WorkflowIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { WorkflowRunStatus } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useOps } from "./use-ops";

export type OpsScreenProps = ReturnType<typeof useOps>;

const RUN_TONE: Record<WorkflowRunStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  completed: "ok",
  failed: "error",
  cancelled: "muted",
  terminated: "muted",
  timed_out: "error",
};

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function formatPct(rate: number | null | undefined): string {
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

/** Live operator view: cluster health, 24h rollouts, alerts, runs, audit. */
export function OpsScreen({
  clusters: clusterList,
  clustersLoading,
  clustersError,
  onRetryClusters,
  metrics: m,
  metricsLoading,
  metricsError,
  onRetryMetrics,
  alerts: alertList,
  alertsLoading,
  alertsError,
  onRetryAlerts,
  audit: auditList,
  auditLoading,
  auditError,
  onRetryAudit,
  runs: runList,
  runsLoading,
  runsError,
  onRetryRuns,
}: OpsScreenProps) {
  const fmt = useFormatters();
  // Active clusters with a recent capabilities probe count as "active";
  // those that have never been probed or were disabled are "degraded".
  const clusterTotal = clusterList.length;
  const clusterActive = clusterList.filter((c) => c.isActive && c.capabilitiesProbedAt).length;
  const clusterDegraded = clusterList.filter((c) => c.isActive && !c.capabilitiesProbedAt).length;
  const clusterDisconnected = clusterList.filter((c) => !c.isActive).length;

  return (
    <PageShell
      title="Operations"
      description="Live operator view — cluster health, rollouts in the last 24 hours, unresolved alerts, and the recent audit trail."
    >
      {/* ─── top KPI strip ─────────────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiTile
          icon={LayersIcon}
          label="Clusters"
          error={clustersError}
          onRetry={onRetryClusters}
          value={clustersLoading ? null : clusterTotal}
          sub={
            clustersLoading
              ? null
              : `${clusterActive} active · ${clusterDegraded} degraded · ${clusterDisconnected} disconnected`
          }
          href="/clusters"
        />
        <KpiTile
          icon={RocketIcon}
          label="Deploys · 24h"
          error={metricsError}
          onRetry={onRetryMetrics}
          value={metricsLoading ? null : (m?.total ?? 0)}
          sub={
            metricsLoading
              ? null
              : `${m?.succeeded ?? 0} ok · ${m?.failed ?? 0} failed · ${m?.inFlight ?? 0} in flight`
          }
          href="/deployments"
        />
        <KpiTile
          icon={CheckCircle2Icon}
          label="Success rate"
          error={metricsError}
          onRetry={onRetryMetrics}
          value={metricsLoading ? null : formatPct(m?.successRate)}
          sub={
            metricsLoading
              ? null
              : `p95 ${formatDuration(m?.p95DurationSeconds)} · mean ${formatDuration(m?.meanDurationSeconds)}`
          }
          href="/deployments"
        />
        <KpiTile
          icon={BellIcon}
          label="Unresolved alerts"
          error={alertsError}
          onRetry={onRetryAlerts}
          value={alertsLoading ? null : alertList.length}
          sub={
            alertsLoading
              ? null
              : alertList.length === 0
                ? "All clear"
                : `${alertList.filter((a) => SEVERITY_TONE[a.severity] === "error").length} critical`
          }
          href="/alerts"
          tone={alertList.length > 0 ? "warn" : undefined}
        />
      </div>

      {/* ─── cluster fleet ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <LayersIcon className="size-4" /> Cluster fleet
            </CardTitle>
            <CardDescription>
              Tenant runtime clusters registered to this Astrolift install.
            </CardDescription>
          </div>
          <Link
            href="/clusters"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            All clusters <ExternalLinkIcon className="size-3" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {clustersError ? (
            <QueryError
              title="Could not load cluster fleet"
              error={clustersError}
              onRetry={onRetryClusters}
            />
          ) : clustersLoading && clusterList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : clusterList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<LayersIcon className="size-5" />}
                title="No clusters registered"
                description="Register a tenant runtime cluster to start receiving deploys."
                actionHref="/clusters"
                actionLabel="Register cluster"
              />
            </div>
          ) : (
            <ul className="divide-y">
              {clusterList.slice(0, 6).map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 px-6 py-3">
                  <div className="flex items-center gap-3">
                    <StatusDot
                      status={!c.isActive ? "muted" : c.capabilitiesProbedAt ? "ok" : "warn"}
                    />
                    <div>
                      <Link href={`/clusters/${c.slug}`} className="font-medium hover:underline">
                        {c.name}
                      </Link>
                      <div className="text-muted-foreground font-mono text-xs">
                        {c.slug} · {c.providerPluginSlug} · {c.region}
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {c.authMethod}
                    </Badge>
                    {!c.isActive ? (
                      <Badge variant="secondary" className="text-xs">
                        disabled
                      </Badge>
                    ) : c.capabilitiesProbedAt ? (
                      <Badge className="bg-success/15 text-success-fg text-xs">active</Badge>
                    ) : (
                      <Badge className="bg-warning/15 text-warning-fg text-xs">not probed</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ─── alerts + workflow runs (two-col) ──────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <BellIcon className="size-4" /> Unresolved alerts
              </CardTitle>
              <CardDescription>
                Alert events that haven&apos;t resolved or been acknowledged.
              </CardDescription>
            </div>
            <Link
              href="/alerts"
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
            >
              All alerts <ExternalLinkIcon className="size-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {alertsError ? (
              <QueryError
                title="Could not load alerts"
                error={alertsError}
                onRetry={onRetryAlerts}
              />
            ) : alertsLoading && alertList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : alertList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<CheckCircle2Icon className="size-5" />}
                  title="All clear"
                  description="No unresolved alerts firing right now."
                />
              </div>
            ) : (
              <ul className="divide-y">
                {alertList.map((a) => {
                  const tone = SEVERITY_TONE[a.severity] ?? "muted";
                  return (
                    <li key={a.id} className="flex items-start gap-3 px-6 py-3">
                      <StatusDot status={tone} className="mt-1.5" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{a.summary || "(no summary)"}</span>
                          <Badge variant="outline" className="text-xs uppercase">
                            {a.severity}
                          </Badge>
                        </div>
                        <div className="text-muted-foreground mt-0.5 text-xs">
                          fired {fmt.formatDateTime(a.firedAt)}
                          {a.acknowledgedAt && (
                            <> · ack&apos;d {fmt.formatDateTime(a.acknowledgedAt)}</>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <WorkflowIcon className="size-4" /> Recent workflow runs
              </CardTitle>
              <CardDescription>
                Temporal workflows kicked off by deploys, previews, or jobs.
              </CardDescription>
            </div>
            <Link
              href="/workflows"
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
            >
              All workflows <ExternalLinkIcon className="size-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {runsError ? (
              <QueryError
                title="Could not load workflow runs"
                error={runsError}
                onRetry={onRetryRuns}
              />
            ) : runsLoading && runList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : runList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<WorkflowIcon className="size-5" />}
                  title="No workflow runs"
                  description="Workflow runs appear here as deploys, previews, and scheduled jobs execute."
                />
              </div>
            ) : (
              <ul className="divide-y">
                {runList.map((r) => (
                  <li key={r.id} className="flex items-start gap-3 px-6 py-3">
                    <StatusDot status={RUN_TONE[r.status]} className="mt-1.5" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm font-medium">{r.workflowKind}</span>
                        <Badge variant="secondary" className="text-xs capitalize">
                          {r.status.replace(/_/g, " ")}
                        </Badge>
                      </div>
                      <div className="text-muted-foreground mt-0.5 text-xs">
                        {r.startedAt && (
                          <span className="inline-flex items-center gap-1">
                            <ClockIcon className="size-3" />
                            started {fmt.formatDateTime(r.startedAt)}
                          </span>
                        )}
                        {r.endedAt && <> · ended {fmt.formatDateTime(r.endedAt)}</>}
                      </div>
                      {r.failure != null && Object.keys(r.failure).length > 0 && (
                        <div className="text-destructive mt-1 truncate font-mono text-xs">
                          {JSON.stringify(r.failure)}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ─── audit trail ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ScrollTextIcon className="size-4" /> Recent audit events
            </CardTitle>
            <CardDescription>
              Permission decisions and state-changing mutations, append-only.
            </CardDescription>
          </div>
          <Link
            href="/administration/audit"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            Full audit log <ExternalLinkIcon className="size-3" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {auditError ? (
            <QueryError
              title="Could not load audit events"
              error={auditError}
              onRetry={onRetryAudit}
            />
          ) : auditLoading && auditList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : auditList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ActivityIcon className="size-5" />}
                title="No audit events yet"
                description="Events appear here as soon as someone makes a mutation against the API."
              />
            </div>
          ) : (
            <ul className="divide-y">
              {auditList.map((e) => (
                <li key={e.id} className="flex flex-wrap items-center gap-3 px-6 py-3 text-sm">
                  <span className="text-muted-foreground w-44 shrink-0 font-mono text-xs">
                    {fmt.formatDateTime(e.occurredAt)}
                  </span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {e.action}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate">
                    {e.actorDisplay || e.actorKind}
                    {e.targetKind ? (
                      <span className="text-muted-foreground">
                        {" → "}
                        <span className="font-mono">
                          {e.targetKind}
                          {e.targetSlug ? `:${e.targetSlug}` : ""}
                        </span>
                      </span>
                    ) : null}
                  </span>
                  <Badge
                    variant="secondary"
                    className={
                      e.decision === "ALLOW"
                        ? "bg-success/15 text-success-fg"
                        : e.decision === "DENY"
                          ? "bg-danger/15 text-danger-fg"
                          : ""
                    }
                  >
                    {e.decision === "ALLOW" ? (
                      <CheckCircle2Icon className="size-3" />
                    ) : e.decision === "DENY" ? (
                      <XCircleIcon className="size-3" />
                    ) : null}
                    {e.decision}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}

interface KpiTileProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: React.ReactNode | null;
  sub: React.ReactNode | null;
  href: string;
  tone?: "warn" | "error";
  error?: { message: string } | null;
  onRetry?: () => void;
}

function KpiTile({ icon: Icon, label, value, sub, href, tone, error, onRetry }: KpiTileProps) {
  if (error)
    return (
      <Card>
        <CardContent className="p-4">
          <QueryError
            title={`Could not load ${label.toLowerCase()}`}
            error={error}
            onRetry={onRetry}
          />
        </CardContent>
      </Card>
    );
  const accent =
    tone === "error"
      ? "border-danger-border bg-danger/5"
      : tone === "warn"
        ? "border-warning-border bg-warning/5"
        : "";
  return (
    <Link href={href} className="contents">
      <Card className={`hover:bg-accent/40 transition-colors ${accent}`}>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
          <div className="bg-primary/10 text-primary rounded-md p-1.5">
            <Icon className="h-4 w-4" />
          </div>
        </CardHeader>
        <CardContent>
          {value == null ? (
            <>
              <Skeleton className="h-8 w-16" />
              <Skeleton className="mt-1 h-3 w-32" />
            </>
          ) : (
            <p className="text-2xl font-bold tabular-nums">{value}</p>
          )}
          {value != null && sub != null && (
            <p className="text-muted-foreground mt-1 text-xs">{sub}</p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
