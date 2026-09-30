"use client";

import { useQuery } from "@apollo/client/react";
import { useEffect, useMemo, useState } from "react";

import { GET_APP_DEPLOYMENT_ACTIVITY } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP_ALERT_SUMMARY } from "@/graphql/operations/alerts.queries";

type DeploymentActivity = Pick<AstroliftDeployment, "id" | "status" | "createdAt" | "startedAt">;
interface DeploymentsResp {
  astroliftDeploymentsPage: { items: DeploymentActivity[]; nextCursor: string | null };
}
interface AlertSummaryResp {
  astroliftAlertEventSummary: { unresolvedCount: number; criticalCount: number };
}

export interface SparkPoint {
  ts: string;
  value: number;
}

const DAYS_WINDOW = 14;

/**
 * Deploys-per-day and failure-rate series computed from the recent deploy
 * stream, plus the unresolved alert-event counts. The data half of
 * ObservabilitySectionView.
 */
export function useObservabilitySummary(appSlug: string) {
  const [windowStart] = useState(() => {
    const day = startOfDay(new Date());
    day.setDate(day.getDate() - DAYS_WINDOW + 1);
    return day.toISOString();
  });
  const [pageError, setPageError] = useState<string | null>(null);
  const deps = useQuery<DeploymentsResp>(GET_APP_DEPLOYMENT_ACTIVITY, {
    variables: { appSlug, filter: { startedAfter: windowStart }, after: null },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
    notifyOnNetworkStatusChange: true,
  });
  const after = deps.data?.astroliftDeploymentsPage.nextCursor;
  const { fetchMore } = deps;
  useEffect(() => {
    if (!after || pageError) return;
    let current = true;
    void fetchMore({
      variables: { after },
      updateQuery: (previous, { fetchMoreResult }) => ({
        astroliftDeploymentsPage: {
          ...fetchMoreResult.astroliftDeploymentsPage,
          items: [
            ...previous.astroliftDeploymentsPage.items,
            ...fetchMoreResult.astroliftDeploymentsPage.items,
          ],
        },
      }),
    }).catch((error: unknown) => {
      if (current)
        setPageError(error instanceof Error ? error.message : "Could not load deployment activity");
    });
    return () => {
      current = false;
    };
  }, [after, fetchMore, pageError]);
  useEffect(() => {
    setPageError(null);
  }, [appSlug]);
  const alerts = useQuery<AlertSummaryResp>(GET_APP_ALERT_SUMMARY, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
  });

  const days = DAYS_WINDOW;

  const { deploysSeries, errorSeries, totalDeploys, failedCount } = useMemo(() => {
    return buildSeries(deps.data?.astroliftDeploymentsPage.items ?? [], days);
  }, [deps.data, days]);

  return {
    days,
    deploysLoading: (deps.loading && !deps.data) || Boolean(after),
    error: pageError ?? deps.error?.message ?? alerts.error?.message ?? null,
    onRetry: () => {
      void Promise.allSettled([deps.refetch(), alerts.refetch()]).then(() => setPageError(null));
    },
    deploysSeries,
    errorSeries,
    totalDeploys,
    failedCount,
    unresolvedCount: alerts.data?.astroliftAlertEventSummary.unresolvedCount ?? 0,
    criticalCount: alerts.data?.astroliftAlertEventSummary.criticalCount ?? 0,
    alertsLoading: alerts.loading,
  };
}

export type ObservabilitySummary = ReturnType<typeof useObservabilitySummary>;

function buildSeries(deployments: DeploymentActivity[], days: number) {
  const buckets: Map<string, { total: number; failed: number }> = new Map();
  // Seed each bucket so the chart spans the full window even when there
  // were quiet days — sparklines look broken when they only have data
  // points at the start and end.
  const today = startOfDay(new Date());
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    buckets.set(d.toISOString(), { total: 0, failed: 0 });
  }

  let totalDeploys = 0;
  let failedCount = 0;
  for (const d of deployments) {
    const key = startOfDay(new Date(d.startedAt ?? d.createdAt)).toISOString();
    const cur = buckets.get(key);
    if (!cur) continue;
    cur.total += 1;
    totalDeploys += 1;
    if (d.status === "failed") {
      cur.failed += 1;
      failedCount += 1;
    }
  }

  const deploysSeries: SparkPoint[] = [];
  const errorSeries: SparkPoint[] = [];
  for (const [ts, v] of buckets) {
    deploysSeries.push({ ts, value: v.total });
    errorSeries.push({ ts, value: v.failed });
  }
  return { deploysSeries, errorSeries, totalDeploys, failedCount };
}

function startOfDay(d: Date): Date {
  const c = new Date(d);
  c.setHours(0, 0, 0, 0);
  return c;
}
