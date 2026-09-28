"use client";

import { useQuery } from "@apollo/client/react";

import type { CursorPage } from "@/components/data-table";
import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { LIST_SCHEDULED_JOB_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

import { JOB_RUNS_LIST, jobRunsVariables, narrowJobRuns } from "./jobs-list";

interface JobRunsPageResp {
  astroliftScheduledJobRunsPage: CursorPage<AstroliftScheduledJobRun>;
}

/**
 * Apps › Scheduled jobs › Runs: one cursor page of
 * `astroliftScheduledJobRunsPage`, polled, new runs held behind the pill.
 * App, job and environment go to the server; status narrows a wider page.
 * The data half of JobRunsScreen.
 */
export function useJobRuns() {
  const list = useListState(JOB_RUNS_LIST);
  const { state } = list;
  const variables = jobRunsVariables(list.filters, state);
  const query = useQuery<JobRunsPageResp>(LIST_SCHEDULED_JOB_RUNS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftScheduledJobRunsPage;
  const narrowing = Boolean(list.filters.status || list.filters.startedBy);
  const rows = narrowJobRuns(page?.items ?? [], list.filters);
  const held = useHeldRows(rows, (r) => r.id, {
    live: state.after === null,
    resetKey: JSON.stringify(list.filters) + state.q + state.pageSize,
  });

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: query.loading && !data,
    stale: query.loading && !query.data && Boolean(data),
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    nextCursor: page?.nextCursor ?? null,
    totalCount: narrowing ? null : (page?.totalCount ?? null),
  };
}
