"use client";

/**
 * Cluster > Health: pod-phase rollup + recent warning events + per-
 * Deployment readiness. Sourced from the driver's kube API client; polls
 * every 30s. Empty payloads (no creds, unreachable apiserver, bare-metal)
 * surface as copy rather than errors. The warnings are a Feed in their
 * frame (list rule 5) and the workloads the tab's one list (rules 3, 4).
 */

import { ActivityIcon, RocketIcon, ServerIcon, ServerOffIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { Feed } from "@/components/feed/Feed";
import { ListPage } from "@/components/list/ListPage";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import { workloadState } from "./cluster-workloads-list";
import type { ClusterEvent, WorkloadRow } from "./types";
import type { useClusterHealth } from "./use-cluster-health";
import type { useClusterWorkloadList } from "./use-cluster-workload-health";

export interface ClusterHealthBodyProps {
  health: Omit<ReturnType<typeof useClusterHealth>, "moreEvents"> & {
    /** Load older warnings (useGrowingLimit); absent, the feed ends. */
    moreEvents?: ReturnType<typeof useClusterHealth>["moreEvents"];
  };
  workloads: ReturnType<typeof useClusterWorkloadList>;
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
// available (no creds / unreachable apiserver / bare-metal); UI surfaces
// that case rather than erroring out.

const PHASE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  Running: "default",
  Succeeded: "secondary",
  Pending: "outline",
  Failed: "destructive",
  Unknown: "outline",
};

function HealthLiveCard({
  pods,
  events,
  loading,
  error,
  refetch,
  moreEvents,
}: ClusterHealthBodyProps["health"]) {
  const fmt = useFormatters();
  const nothing = pods.length === 0 && events.length === 0;

  return (
    <Panel
      icon={<ActivityIcon className="size-4" />}
      title="Live health"
      description={
        <>
          Pod-phase rollup + recent Warning events across the platform&apos;s managed namespaces.
          Sourced directly from the cluster driver; polls every 30s.
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
              title: "No pod data available",
              description:
                "The driver couldn't reach the apiserver, or no astrolift-managed pods are running.",
            }
          : null
      }
    >
      <div className="space-y-4">
        <div>
          <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">Pods</h3>
          {pods.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No pod data available — the driver couldn&apos;t reach the apiserver, or no
              astrolift-managed pods are running.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pods.map((p) => (
                <Badge
                  key={`${p.namespace}-${p.phase}`}
                  variant={PHASE_VARIANT[p.phase] ?? "outline"}
                  className="gap-1.5"
                >
                  <span className="text-2xs font-mono">{p.namespace}</span>
                  <span className="opacity-60">·</span>
                  <span>{p.phase}</span>
                  <span className="opacity-60">·</span>
                  <span className="font-mono tabular-nums">{p.count}</span>
                </Badge>
              ))}
            </div>
          )}
        </div>
        <div>
          <h3 className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
            Recent warnings
          </h3>
          <Feed<ClusterEvent>
            label="Recent warnings"
            items={events}
            keyOf={(e) => `${e.namespace}-${e.name}-${e.lastSeen ?? ""}`}
            {...moreEvents}
            dense
            empty={{
              icon: <ActivityIcon className="size-5" />,
              title: "No recent warning events",
              description: "A green Live health state is the expected baseline.",
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
                  {e.count > 1 && (
                    <Badge variant="outline" className="text-2xs font-mono">
                      ×{e.count}
                    </Badge>
                  )}
                  <span className="text-muted-foreground ml-auto font-mono text-xs">
                    {e.lastSeen ? fmt.formatDateTime(e.lastSeen) : "—"}
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
}: ClusterHealthBodyProps["workloads"]) {
  const fmt = useFormatters();

  const columns: Column<WorkloadRow>[] = [
    {
      id: "workload",
      header: "Workload",
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
      header: "Namespace",
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
      header: "Ready / desired",
      sortKey: "deficit",
      align: "right",
      cell: (row) => {
        const state = workloadState(row);
        const tone =
          state === "ready"
            ? "text-success-fg"
            : state === "down"
              ? "text-destructive"
              : "text-warning-fg";
        return (
          <span className={"font-mono whitespace-nowrap tabular-nums " + tone}>
            {row.readyReplicas} / {row.desiredReplicas}
          </span>
        );
      },
    },
    {
      id: "restarts",
      header: "Restarts 24h",
      sortKey: "restarts",
      align: "right",
      cell: (row) => (
        <span
          className={
            "font-mono tabular-nums " +
            (row.restartCount24h === 0
              ? "text-muted-foreground"
              : row.restartCount24h < 5
                ? "text-warning-fg"
                : "text-destructive")
          }
        >
          {row.restartCount24h}
        </span>
      ),
    },
    {
      id: "deployed",
      header: "Last deploy",
      sortKey: "deployed",
      align: "right",
      cellClassName: "text-muted-foreground font-mono text-xs whitespace-nowrap",
      cell: (row) =>
        row.lastImageDeployedAt ? (
          <span className="inline-flex items-center gap-1">
            <RocketIcon className="size-3" />
            {fmt.formatDateTime(row.lastImageDeployedAt)}
          </span>
        ) : (
          <span className="opacity-60">never</span>
        ),
    },
  ];

  return (
    <Panel
      icon={<ServerIcon className="size-4" />}
      title="Workload health"
      description={
        <>
          Per-Deployment readiness + 24h restart counts across the platform&apos;s managed
          namespaces. Sorted most-broken first; polls every 30s.
        </>
      }
    >
      <ListPage<WorkloadRow>
        embedded
        list={list}
        label="Workloads"
        columns={columns}
        rows={rows}
        getRowId={(row) => `${row.namespace}/${row.workloadName}`}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <ServerOffIcon className="size-5" />,
          title: "No workload data available",
          description:
            "The driver couldn't reach the apiserver, or no Deployments are running in the platform's managed namespaces yet.",
        }}
      />
    </Panel>
  );
}
