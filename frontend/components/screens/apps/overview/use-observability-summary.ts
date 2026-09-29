"use client";

import { useQuery } from "@apollo/client/react";
import { useMemo } from "react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface AlertEventsResp {
  astroliftAlertEvents: Array<{
    id: string;
    severity: string;
    firedAt: string;
    resolvedAt: string | null;
    acknowledgedAt: string | null;
    summary: string;
  }>;
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
  const deps = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 100 },
    fetchPolicy: "cache-and-network",
  });
  const alerts = useQuery<AlertEventsResp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: true, limit: 50 },
    fetchPolicy: "cache-and-network",
  });

  const days = DAYS_WINDOW;

  const { deploysSeries, errorSeries, totalDeploys, failedCount } = useMemo(() => {
    return buildSeries(deps.data?.astroliftDeployments ?? [], days);
  }, [deps.data, days]);

  const unresolved = alerts.data?.astroliftAlertEvents ?? [];
  const critical = unresolved.filter((a) => a.severity === "critical").length;

  return {
    days,
    deploysLoading: deps.loading,
    deploysSeries,
    errorSeries,
    totalDeploys,
    failedCount,
    unresolvedCount: unresolved.length,
    criticalCount: critical,
    alertsLoading: alerts.loading,
  };
}

export type ObservabilitySummary = ReturnType<typeof useObservabilitySummary>;

function buildSeries(deployments: AstroliftDeployment[], days: number) {
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
    const key = startOfDay(new Date(d.createdAt)).toISOString();
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
