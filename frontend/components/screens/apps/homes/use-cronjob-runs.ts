"use client";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { LIST_SCHEDULED_JOB_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

interface RunsPageResp {
  astroliftScheduledJobRunsPage: CursorPage<AstroliftScheduledJobRun>;
}

/**
 * The run history behind CronjobHomeScreen.
 *
 * Scoped to this workload on the server (#1512). It used to fetch the
 * app's 30 newest runs and keep this workload's in the browser, so a job
 * firing less often than its neighbours showed "no runs recorded yet"
 * while its runs existed. Poll so a fresh run surfaces without a reload.
 * No `urlKey`: several cronjob pages share the app's URL space.
 */
export function useCronjobRuns(appSlug: string, workloadSlug: string) {
  const table = useCursorTable<AstroliftScheduledJobRun>({
    query: LIST_SCHEDULED_JOB_RUNS_PAGE,
    variables: { appSlug, workloadSlug },
    extract: (d) => (d as RunsPageResp | undefined)?.astroliftScheduledJobRunsPage,
    searchVariable: "search",
    pageSize: 10,
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  return { table };
}
