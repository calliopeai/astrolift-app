"use client";

/**
 * Cluster > Health: pod-phase rollup + recent warning events + per-
 * Deployment readiness. Sourced from the driver's kube API client; polls
 * every 30s. Empty observations do not establish reachability or health. The warnings are a Feed in their
 * frame (list rule 5) and the workloads the tab's one list (rules 3, 4).
 */

import { ActivityIcon, RocketIcon, ServerIcon, ServerOffIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { Feed } from "@/components/feed/Feed";
import { ListPage } from "@/components/list/ListPage";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import { workloadState } from "./cluster-workloads-list";
import { observedHealthCount, observedHealthDate, observedReadiness } from "./health-observations";
import type { ClusterEvent, WorkloadRow } from "./types";
import type { useClusterHealth } from "./use-cluster-health";
import type { useClusterWorkloadList } from "./use-cluster-workload-health";

export interface ClusterHealthBodyProps {
  health: Omit<ReturnType<typeof useClusterHealth>, "moreEvents"> & {
    /** Load older warnings (useGrowingLimit); absent, the feed ends. */
    moreEvents?: ReturnType<typeof useClusterHealth>["moreEvents"];
  };
  workloads: Omit<ReturnType<typeof useClusterWorkloadList>, "observation"> & {
    observation?: { hasRows: boolean; loading: boolean; error: string | null };
  };
}

/** The Health tab body: the live health panel over the workload health panel. */
export function ClusterHealthBody({ health, workloads }: ClusterHealthBodyProps) {
  return (
    <PanelGrid>
      <HealthLiveCard {...health} />
      <HealthWorkloadCard {...workloads} />
    </PanelGrid>
  );
}

// ─── Live health card (#68 slice 1) ──────────────────────────────────────
// Backed by ``astroliftClusterHealth`` — driver calls into the kube API
// for pod phases + recent Warning events. Empty payload = no data
// available; the UI does not turn absence into a reachability diagnosis.

const PHASE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  Running: "default",
  Succeeded: "secondary",
  Pending: "outline",
  Failed: "destructive",
  Unknown: "outline",
  CrashLoopBackOff: "destructive",
};

const PHASE_LABEL = {
  Running: "running",
  Succeeded: "succeeded",
  Pending: "pending",
  Failed: "failed",
  Unknown: "phaseUnknown",
  CrashLoopBackOff: "crashLoop",
} as const;

function HealthLiveCard({
  pods,
  events,
  loading,
  error,
  refetch,
  moreEvents,
}: ClusterHealthBodyProps["health"]) {
  const fmt = useFormatters();
  const t = useTranslations("clusterHealthTab");
  const h = useTranslations("clusterHealth");
  const nothing = pods.length === 0 && events.length === 0;

  return (
    <Panel
      icon={<ActivityIcon className="size-4" />}
      title={h("liveTitle")}
      description={
        <>
          {t("liveHelp")}
          {!nothing && (loading || error) && (
            <HealthRefreshNotice loading={loading} error={error} refetch={refetch} />
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
              title: h("noHealth"),
              description: h("noHealthHelp"),
            }
          : null
      }
    >
      <div className="space-y-4">
        <div>
          <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
            {h("podPhases")}
          </h3>
          {pods.length === 0 ? (
            <p className="text-muted-foreground text-sm">{h("noPods")}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pods.map((p) => (
                <Badge
                  key={`${p.namespace}-${p.phase}`}
                  variant={
                    Object.hasOwn(PHASE_VARIANT, p.phase) && observedHealthCount(p.count)
                      ? PHASE_VARIANT[p.phase]
                      : "outline"
                  }
                  title={p.phase}
                  className={`gap-1.5 ${!observedHealthCount(p.count) || !Object.hasOwn(PHASE_VARIANT, p.phase) ? "text-muted-foreground" : ""}`}
                >
                  <span className="text-2xs font-mono">{p.namespace}</span>
                  <span className="opacity-60">·</span>
                  <span>
                    {Object.hasOwn(PHASE_LABEL, p.phase)
                      ? h(PHASE_LABEL[p.phase as keyof typeof PHASE_LABEL])
                      : p.phase || h("unknown")}
                  </span>
                  <span className="opacity-60">·</span>
                  <span className="font-mono tabular-nums">
                    {observedHealthCount(p.count) ? fmt.formatNumber(p.count) : h("unknown")}
                  </span>
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div>
          <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
            {t("warnings")}
          </h3>
          <Feed<ClusterEvent>
            label={t("warnings")}
            items={events}
            keyOf={(e) => `${e.namespace}-${e.name}-${e.lastSeen ?? ""}`}
            {...moreEvents}
            dense
            empty={{
              icon: <ActivityIcon className="size-5" />,
              title: t("noWarnings"),
              description: t("noWarningsHelp"),
            }}
            renderItem={(e) => (
              <div className="min-w-0 text-sm">
                <div className="flex min-w-0 flex-wrap items-baseline gap-2">
                  <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                    {e.reason}
                  </code>
                  <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                    {e.involvedObject}
                  </span>
                  {(!observedHealthCount(e.count) || e.count > 1) && (
                    <Badge variant="outline" className="text-2xs font-mono">
                      {observedHealthCount(e.count)
                        ? `×${fmt.formatNumber(e.count)}`
                        : h("unknown")}
                    </Badge>
                  )}
                  <span className="text-muted-foreground ml-auto font-mono text-xs">
                    {observedHealthDate(e.lastSeen)
                      ? fmt.formatDateTime(observedHealthDate(e.lastSeen)!)
                      : e.lastSeen
                        ? h("unknown")
                        : "—"}
                  </span>
                </div>
                <p className="text-muted-foreground mt-1 line-clamp-2 text-xs [overflow-wrap:anywhere]">
                  {e.message}
                </p>
              </div>
            )}
          />
        </div>
      </div>
    </Panel>
  );
}

// ─── Workload health card (#362 slice 3) ─────────────────────────────
// Backed by ``astroliftClusterWorkloadHealth`` — per-Deployment view
// of desired vs ready replicas + 24h restart counts + last rollout.
// The pod-phase card above answers "is anything red"; this answers
// "which workload is red" and lets the operator triage without
// clicking through to ``kubectl`` / a dashboard.

function HealthWorkloadCard({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  observation,
}: ClusterHealthBodyProps["workloads"]) {
  const fmt = useFormatters();
  const t = useTranslations("clusterHealthTab");
  const h = useTranslations("clusterHealth");

  const columns: Column<WorkloadRow>[] = [
    {
      id: "workload",
      header: t("workload"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (row) => (
        <code className="block truncate font-mono text-xs" title={row.workloadName}>
          {row.workloadName}
        </code>
      ),
    },
    {
      id: "namespace",
      header: t("namespace"),
      sortKey: "namespace",
      cellClassName: "text-muted-foreground max-w-48",
      cell: (row) => (
        <span className="block truncate font-mono text-xs" title={row.namespace}>
          {row.namespace}
        </span>
      ),
    },
    {
      id: "ready",
      header: t("readiness"),
      sortKey: "deficit",
      align: "right",
      cell: (row) => {
        const state = workloadState(row);
        const tone =
          state === "unknown" || state === "inactive"
            ? "text-muted-foreground"
            : state === "ready"
              ? "text-success-fg"
              : state === "down"
                ? "text-destructive"
                : "text-warning-fg";
        return (
          <span className={"font-mono whitespace-nowrap tabular-nums " + tone}>
            {observedReadiness(row.readyReplicas, row.desiredReplicas)
              ? `${fmt.formatNumber(row.readyReplicas)} / ${fmt.formatNumber(row.desiredReplicas)}`
              : h("unknown")}
          </span>
        );
      },
    },
    {
      id: "restarts",
      header: t("restarts24h"),
      sortKey: "restarts",
      align: "right",
      cell: (row) => (
        <span
          className={
            "font-mono tabular-nums " +
            (!observedHealthCount(row.restartCount24h) || row.restartCount24h === 0
              ? "text-muted-foreground"
              : row.restartCount24h < 5
                ? "text-warning-fg"
                : "text-destructive")
          }
        >
          {observedHealthCount(row.restartCount24h)
            ? fmt.formatNumber(row.restartCount24h)
            : h("unknown")}
        </span>
      ),
    },
    {
      id: "deployed",
      header: t("deployed"),
      sortKey: "deployed",
      align: "right",
      cellClassName: "text-muted-foreground font-mono text-xs whitespace-nowrap",
      cell: (row) =>
        observedHealthDate(row.lastImageDeployedAt) ? (
          <span className="inline-flex items-center gap-1">
            <RocketIcon className="size-3" />
            {fmt.formatDateTime(row.lastImageDeployedAt)}
          </span>
        ) : (
          <span className="opacity-60">
            {row.lastImageDeployedAt ? h("unknown") : t("noDeployTime")}
          </span>
        ),
    },
  ];

  const hasRows = observation?.hasRows ?? rows.length > 0;
  const refreshing = observation?.loading ?? loading;
  const refreshError = observation?.error ?? error?.message ?? null;

  return (
    <Panel
      icon={<ServerIcon className="size-4" />}
      title={h("workloadTitle")}
      description={
        <>
          {t("workloadHelp")}
          {hasRows && (refreshing || refreshError) && (
            <HealthRefreshNotice loading={refreshing} error={refreshError} refetch={onRetry} />
          )}
        </>
      }
    >
      <ListPage<WorkloadRow>
        embedded
        list={list}
        label={t("workloads")}
        columns={columns}
        rows={rows}
        getRowId={(row) => `${row.namespace}/${row.workloadName}`}
        loading={loading && !hasRows}
        error={hasRows ? null : error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <ServerOffIcon className="size-5" />,
          title: h("noDeployments"),
          description: h("noDeploymentsHelp"),
        }}
      />
    </Panel>
  );
}

function HealthRefreshNotice({
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
