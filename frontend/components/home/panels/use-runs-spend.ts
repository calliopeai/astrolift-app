"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useNow } from "@/components/screens/deployments/run-support";
import { GET_COST_TREND } from "@/graphql/billing/billing.queries";
import type { AstroliftCostTrendPoint } from "@/graphql/billing/billing.types";

import { runsPerDay, spendPerDay, utcDay } from "./apps-agents-model";
import { useHomeAgentRuns, usePanelAccess } from "./home-reads";
import type { RunsSpendPanelViewProps } from "./RunsSpendPanel";

/**
 * The window the Runs page reads for its agent source (`useRuns`'s
 * SOURCE_LIMIT), so this panel and Agents › Runs read one cache entry.
 */
const RUN_WINDOW = 100;

interface CostTrendResp {
  astroliftCostTrend: AstroliftCostTrendPoint[];
}

/**
 * Runs & spend's data: the Runs page's window of agent runs bucketed by
 * day, and the organization's daily cost for the same week when the viewer
 * holds `billing.read`. There is no per-agent spend yet (see needsBackend).
 */
export function useRunsSpend(): Omit<RunsSpendPanelViewProps, "panel"> {
  const { can } = usePanelAccess();
  const billing = can("billing.read");
  const runs = useHomeAgentRuns(null, { limit: RUN_WINDOW });
  const trend = useQuery<CostTrendResp>(GET_COST_TREND, {
    variables: { window: "D7", days: null },
    fetchPolicy: "cache-and-network",
    skip: !billing,
  });
  // Day buckets move at midnight, not on every render.
  const now = useNow(true, 60_000);
  const days = React.useMemo(() => runsPerDay(runs.runs, now), [runs.runs, now]);
  const points = trend.data?.astroliftCostTrend;
  const perDay = billing && points ? spendPerDay(points, days) : null;
  const spend =
    perDay && points
      ? {
          perDay,
          totalCents: perDay.reduce((a, b) => a + b, 0),
          currency: points[0]?.currency ?? "USD",
        }
      : null;
  return {
    days,
    capped: runs.total !== null && runs.total > runs.runs.length && inWindow(runs.runs, days),
    spend,
    spendError: billing && trend.error && !points ? trend.error.message : null,
    loading: runs.loading,
    error: runs.error,
    onRetry: runs.onRetry,
  };
}

/** True when the oldest run in hand is still inside the week, so older ones may be too. */
function inWindow(runs: { createdAt: string }[], days: { date: string }[]): boolean {
  const oldest = runs[runs.length - 1];
  if (!oldest || days.length === 0) return false;
  const t = Date.parse(oldest.createdAt);
  return !Number.isNaN(t) && utcDay(t) >= days[0]!.date;
}
