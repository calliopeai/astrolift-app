"use client";

import { useQuery } from "@apollo/client/react";

import type { CursorPage } from "@/components/data-table";
import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { LIST_COMMAND_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";

import { COMMAND_RUNS_LIST, commandRunsVariables, narrowCommandRuns } from "./jobs-list";

interface CommandRunsPageResp {
  astroliftCommandRunsPage: CursorPage<AstroliftCommandRun>;
}

/**
 * Apps › Scheduled jobs › Commands: one cursor page of
 * `astroliftCommandRunsPage`, polled, new runs held behind the pill. Mine
 * and Invoked by narrow a wider page by username. The data half of
 * CommandRunsScreen.
 */
export function useCommandRuns() {
  const list = useListState(COMMAND_RUNS_LIST);
  const { state } = list;
  const me = useQuery<MeQueryData>(GET_ME).data?.me?.profile?.username ?? null;
  const query = useQuery<CommandRunsPageResp>(LIST_COMMAND_RUNS_PAGE, {
    variables: commandRunsVariables(list.filters, state),
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftCommandRunsPage;
  const narrowing = Boolean(list.filters.invokedBy);
  const rows = narrowCommandRuns(page?.items ?? [], list.filters, me);
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
