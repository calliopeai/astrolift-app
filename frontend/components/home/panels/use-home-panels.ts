"use client";

/**
 * The data half of the Builder and Operator panels (spec 44 §4.3). One hook
 * per panel, each with its own query (rule 2): Home mounts a panel only when
 * the layout lists it and the person may see it, so a hook never runs for a
 * panel that is not on screen. A hook that serves two panels (the agent run
 * window behind Agent runs and the KPI strip) is a shared read in
 * home-reads.ts, so panels on one layout make one request between them.
 * Deployments is the Recent deployments panel under the Builder title.
 */

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { CursorPage } from "@/components/data-table";
import { useGrowingLimit } from "@/components/feed/use-growing-limit";
import { useHomePresentation } from "./use-home-presentation";
import type { AlertEvent } from "@/components/screens/alerts/use-alerts";
import type { ClusterRow } from "@/components/screens/clusters/list/use-clusters-list";
import { LIST_CLUSTERS_PAGE } from "@/graphql/clusters/clusters.queries";
import { GET_DEPLOYMENT_METRICS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeploymentMetrics } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_ALERT_EVENTS_PAGE } from "@/graphql/operations/alerts.queries";
import { LIST_WORKFLOW_RUNS } from "@/graphql/operations/operations.queries";
import type { AstroliftWorkflowRun } from "@/graphql/operations/operations.types";

import {
  alertsFiringFirst,
  clustersWorstFirst,
  type KpisPanelData,
  kpiFigures,
} from "./builder-operator-model";
import { type HomeAgentTask, type HomeRead, useHomeAgentRuns, usePanelAccess } from "./home-reads";

/** The KPI period, and the deployment metrics window it asks for. */
export const KPI_WINDOW_DAYS = 30;

const errorOf = (e: { message: string } | undefined, hasData: boolean) =>
  e && !hasData ? e.message : null;

// ---------------------------------------------------------------- agent runs

/**
 * The newest agent runs, shared by Agent runs and the KPI strip: the Runs
 * list's own agent read (its SOURCE_LIMIT of 100, every status), so the two
 * panels make one request and opening Agents › Runs is a cache hit.
 */
export const RUN_WINDOW = 100;

/** Agent runs: the five newest of the run window, with the total. */
export function useAgentRunsPanel(): {
  rows: HomeAgentTask[];
  count: number | null;
} & HomeRead {
  const { runs, total, ...read } = useHomeAgentRuns(null, { limit: RUN_WINDOW });
  return { rows: runs.slice(0, 5), count: total, ...read };
}

// ---------------------------------------------------------------- KPIs

interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}

/**
 * The KPI strip: deploy counts and timing from the deployment metrics, runs
 * from the shared run window. Each figure's
 * query runs only when the person may see it; a figure they may not see is
 * left out of the strip.
 */
export function useKpisPanel(): KpisPanelData {
  const presentation = useHomePresentation();
  const { canView, can } = usePanelAccess();
  const apps = canView("apps") && can("app.read");
  const agents = canView("agents") && can("agent.read");
  const [now] = React.useState(() => Date.now());

  const metrics = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays: KPI_WINDOW_DAYS },
    fetchPolicy: "cache-and-network",
    skip: !apps,
  });
  const runs = useHomeAgentRuns(null, { limit: RUN_WINDOW, skip: !agents });

  const m = (metrics.data ?? metrics.previousData)?.astroliftDeploymentMetrics ?? null;
  const pending = [apps && metrics.loading && !m, agents && runs.loading].some(Boolean);
  const failed = [
    apps ? errorOf(metrics.error, Boolean(m)) : null,
    agents ? runs.error : null,
  ].filter((e): e is string => e !== null);
  const shown = [apps, agents].filter(Boolean).length;

  return {
    windowDays: KPI_WINDOW_DAYS,
    figures: kpiFigures(
      {
        metrics: apps ? m : undefined,
        runs: agents
          ? {
              runs: runs.runs,
              capped: runs.total !== null && runs.total > runs.runs.length,
              now,
              loading: runs.loading,
            }
          : undefined,
        windowDays: KPI_WINDOW_DAYS,
      },
      presentation
    ),
    loading: pending,
    // Only a strip with nothing to show is an error; one failed source is a dash.
    error: shown > 0 && failed.length === shown ? failed[0]! : null,
    onRetry: () => {
      if (apps) void metrics.refetch();
      if (agents) runs.onRetry();
    },
  };
}

// ---------------------------------------------------------------- alerts

interface AlertEventsPageResp {
  astroliftAlertEventsPage: CursorPage<AlertEvent>;
}

/** Alerts: the firing ones, worst first, and how many are firing. */
export function useAlertsPanel() {
  const q = useQuery<AlertEventsPageResp>(LIST_ALERT_EVENTS_PAGE, {
    variables: { unresolvedOnly: true, search: null, limit: 5, after: null },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });
  const page = (q.data ?? q.previousData)?.astroliftAlertEventsPage;
  return {
    rows: alertsFiringFirst(page?.items ?? []),
    count: page?.totalCount ?? null,
    loading: q.loading && !page,
    error: errorOf(q.error, Boolean(page)),
    onRetry: () => void q.refetch(),
  };
}

// ---------------------------------------------------------------- clusters

interface ClustersPageResp {
  astroliftClustersPage: CursorPage<ClusterRow>;
}

/**
 * Clusters: the fleet, the least healthy first. The variables are the ones
 * Admin › Clusters sends on a cold load (its WALK_LIMIT), so the two share
 * one cache entry and the panel ranks the whole fleet, not a page of it.
 */
export function useClustersPanel() {
  const q = useQuery<ClustersPageResp>(LIST_CLUSTERS_PAGE, {
    variables: { search: null, limit: 200 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });
  const page = (q.data ?? q.previousData)?.astroliftClustersPage;
  const fleet = React.useMemo(() => page?.items ?? [], [page]);
  return {
    rows: React.useMemo(() => clustersWorstFirst(fleet), [fleet]),
    count: page?.totalCount ?? (page ? fleet.length : null),
    loading: q.loading && !page,
    error: errorOf(q.error, Boolean(page)),
    onRetry: () => void q.refetch(),
  };
}

// ---------------------------------------------------------------- platform activity

interface WorkflowRunsResp {
  astroliftWorkflowRuns: AstroliftWorkflowRun[];
}

/**
 * Platform activity: the platform's own workflow runs (deploys, onboarding,
 * cluster bring-in), newest first, loading older ones as the reader nears
 * the end. The field takes a limit and no cursor, so it grows the limit
 * (see useGrowingLimit and the report's needsBackend).
 */
export function usePlatformActivityPanel() {
  const grow = useGrowingLimit(10);
  const q = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_RUNS, {
    variables: { limit: grow.limit },
    fetchPolicy: "cache-and-network",
  });
  const data = q.data ?? q.previousData;
  const items = data?.astroliftWorkflowRuns ?? [];
  return {
    items,
    loading: q.loading && !data,
    error: errorOf(q.error, Boolean(data)),
    onRetry: () => void q.refetch(),
    ...grow.feed(items.length, q.loading),
  };
}
