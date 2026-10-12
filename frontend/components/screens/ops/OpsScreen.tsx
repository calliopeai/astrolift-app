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
import { useFormatter, useTranslations } from "next-intl";
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
  const format = useFormatter();
  const t = useTranslations("opsScreen");
  const formatDuration = (seconds: number | null | undefined): string => {
    if (seconds == null) return "—";
    if (seconds < 60) return t("durationSeconds", { s: Math.round(seconds) });
    return t("durationMinutes", { m: Math.floor(seconds / 60), s: Math.round(seconds % 60) });
  };
  const formatPct = (rate: number | null | undefined): string => {
    if (rate == null) return "—";
    return format.number(rate, {
      style: "percent",
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  };
  // Active clusters with a recent capabilities probe count as "active";
  // those that have never been probed or were disabled are "degraded".
  const clusterTotal = clusterList.length;
  const clusterActive = clusterList.filter((c) => c.isActive && c.capabilitiesProbedAt).length;
  const clusterDegraded = clusterList.filter((c) => c.isActive && !c.capabilitiesProbedAt).length;
  const clusterDisconnected = clusterList.filter((c) => !c.isActive).length;

  return (
    <PageShell title={t("title")} description={t("description")}>
      {/* ─── top KPI strip ─────────────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiTile
          icon={LayersIcon}
          label={t("clusters")}
          errorTitle={t("clustersError")}
          error={clustersError}
          onRetry={onRetryClusters}
          value={clustersLoading ? null : clusterTotal}
          sub={
            clustersLoading
              ? null
              : t("clustersSub", {
                  active: clusterActive,
                  degraded: clusterDegraded,
                  disconnected: clusterDisconnected,
                })
          }
          href="/clusters"
        />
        <KpiTile
          icon={RocketIcon}
          label={t("deploys")}
          errorTitle={t("deploysError")}
          error={metricsError}
          onRetry={onRetryMetrics}
          value={metricsLoading ? null : (m?.total ?? 0)}
          sub={
            metricsLoading
              ? null
              : t("deploysSub", {
                  ok: m?.succeeded ?? 0,
                  failed: m?.failed ?? 0,
                  inFlight: m?.inFlight ?? 0,
                })
          }
          href="/deployments"
        />
        <KpiTile
          icon={CheckCircle2Icon}
          label={t("successRate")}
          errorTitle={t("successRateError")}
          error={metricsError}
          onRetry={onRetryMetrics}
          value={metricsLoading ? null : formatPct(m?.successRate)}
          sub={
            metricsLoading
              ? null
              : t("successRateSub", {
                  p95: formatDuration(m?.p95DurationSeconds),
                  mean: formatDuration(m?.meanDurationSeconds),
                })
          }
          href="/deployments"
        />
        <KpiTile
          icon={BellIcon}
          label={t("alerts")}
          errorTitle={t("alertsError")}
          error={alertsError}
          onRetry={onRetryAlerts}
          value={alertsLoading ? null : alertList.length}
          sub={
            alertsLoading
              ? null
              : alertList.length === 0
                ? t("allClear")
                : t("critical", {
                    count: alertList.filter((a) => SEVERITY_TONE[a.severity] === "error").length,
                  })
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
              <LayersIcon className="size-4" /> {t("fleet")}
            </CardTitle>
            <CardDescription>{t("fleetDescription")}</CardDescription>
          </div>
          <Link
            href="/clusters"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            {t("allClusters")} <ExternalLinkIcon className="size-3" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {clustersError ? (
            <QueryError title={t("fleetError")} error={clustersError} onRetry={onRetryClusters} />
          ) : clustersLoading && clusterList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : clusterList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<LayersIcon className="size-5" />}
                title={t("noClusters")}
                description={t("noClustersDescription")}
                actionHref="/clusters"
                actionLabel={t("registerCluster")}
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
                        {t("clusterDisabled")}
                      </Badge>
                    ) : c.capabilitiesProbedAt ? (
                      <Badge className="bg-success/15 text-success-fg text-xs">
                        {t("clusterActive")}
                      </Badge>
                    ) : (
                      <Badge className="bg-warning/15 text-warning-fg text-xs">
                        {t("clusterNotProbed")}
                      </Badge>
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
                <BellIcon className="size-4" /> {t("alerts")}
              </CardTitle>
              <CardDescription>{t("alertsDescription")}</CardDescription>
            </div>
            <Link
              href="/alerts"
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
            >
              {t("allAlerts")} <ExternalLinkIcon className="size-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {alertsError ? (
              <QueryError title={t("alertsError")} error={alertsError} onRetry={onRetryAlerts} />
            ) : alertsLoading && alertList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : alertList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<CheckCircle2Icon className="size-5" />}
                  title={t("allClear")}
                  description={t("allClearDescription")}
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
                          <span className="text-sm font-medium">{a.summary || t("noSummary")}</span>
                          <Badge variant="outline" className="text-xs uppercase">
                            {a.severity}
                          </Badge>
                        </div>
                        <div className="text-muted-foreground mt-0.5 text-xs">
                          {t("fired", { at: fmt.formatDateTime(a.firedAt) })}
                          {a.acknowledgedAt && (
                            <>
                              {" "}
                              · {t("acknowledged", { at: fmt.formatDateTime(a.acknowledgedAt) })}
                            </>
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
                <WorkflowIcon className="size-4" /> {t("runs")}
              </CardTitle>
              <CardDescription>{t("runsDescription")}</CardDescription>
            </div>
            <Link
              href="/workflows"
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
            >
              {t("allWorkflows")} <ExternalLinkIcon className="size-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {runsError ? (
              <QueryError title={t("runsError")} error={runsError} onRetry={onRetryRuns} />
            ) : runsLoading && runList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : runList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<WorkflowIcon className="size-5" />}
                  title={t("noRuns")}
                  description={t("noRunsDescription")}
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
                          {t.has(`runStatus.${r.status}`)
                            ? t(`runStatus.${r.status}`)
                            : r.status.replace(/_/g, " ")}
                        </Badge>
                      </div>
                      <div className="text-muted-foreground mt-0.5 text-xs">
                        {r.startedAt && (
                          <span className="inline-flex items-center gap-1">
                            <ClockIcon className="size-3" />
                            {t("started", { at: fmt.formatDateTime(r.startedAt) })}
                          </span>
                        )}
                        {r.endedAt && <> · {t("ended", { at: fmt.formatDateTime(r.endedAt) })}</>}
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
              <ScrollTextIcon className="size-4" /> {t("audit")}
            </CardTitle>
            <CardDescription>{t("auditDescription")}</CardDescription>
          </div>
          <Link
            href="/administration/audit"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            {t("fullAudit")} <ExternalLinkIcon className="size-3" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {auditError ? (
            <QueryError title={t("auditError")} error={auditError} onRetry={onRetryAudit} />
          ) : auditLoading && auditList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : auditList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ActivityIcon className="size-5" />}
                title={t("noAudit")}
                description={t("noAuditDescription")}
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
  errorTitle: string;
  value: React.ReactNode | null;
  sub: React.ReactNode | null;
  href: string;
  tone?: "warn" | "error";
  error?: { message: string } | null;
  onRetry?: () => void;
}

function KpiTile({
  icon: Icon,
  label,
  errorTitle,
  value,
  sub,
  href,
  tone,
  error,
  onRetry,
}: KpiTileProps) {
  if (error)
    return (
      <Card>
        <CardContent className="p-4">
          <QueryError title={errorTitle} error={error} onRetry={onRetry} />
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
