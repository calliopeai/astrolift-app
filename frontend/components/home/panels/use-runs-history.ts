"use client";

import * as React from "react";

import { useNow } from "@/components/screens/deployments/run-support";

import { runsPerDay, utcDay } from "./apps-agents-model";
import { useHomeAgentRuns } from "./home-reads";
import type { RunsHistoryPanelViewProps } from "./RunsHistoryPanel";

/**
 * The window the Runs page reads for its agent source (`useRuns`'s
 * SOURCE_LIMIT), so this panel and Agents › Runs read one cache entry.
 */
const RUN_WINDOW = 100;

/** The Runs page's bounded window, grouped by day. */
export function useRunsHistory(): Omit<RunsHistoryPanelViewProps, "panel"> {
  const runs = useHomeAgentRuns(null, { limit: RUN_WINDOW });
  // Day buckets move at midnight, not on every render.
  const now = useNow(true, 60_000);
  const days = React.useMemo(() => runsPerDay(runs.runs, now), [runs.runs, now]);
  return {
    days,
    capped: runs.total !== null && runs.total > runs.runs.length && inWindow(runs.runs, days),
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
