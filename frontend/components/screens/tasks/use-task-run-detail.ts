"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import { useNow } from "@/components/screens/deployments/run-support";
import { GET_TASK_RUN, LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

interface TaskRunResp {
  astroliftTaskRun: AstroliftTaskRun | null;
}

interface TaskRunListResp {
  astroliftTaskRuns: AstroliftTaskRun[];
}

const LIVE = new Set(["pending", "running"]);

/**
 * Task run by id (#1106, #1118). Prefers the singular `astroliftTaskRun(id)`
 * query so a cold deep-link to a run outside the 100-row list window still
 * resolves. Falls back to the row already in the cached LIST_TASK_RUNS window
 * for an instant paint; the by-id fetch refreshes in the background and is
 * the source of truth otherwise. Polls and ticks its clock while the run is
 * live. The data half of TaskRunDetail.
 */
export function useTaskRunDetail(id: string) {
  const client = useApolloClient();
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<TaskRunResp>(
    GET_TASK_RUN,
    { variables: { id }, fetchPolicy: "cache-and-network" }
  );

  const cachedFromList = React.useMemo(() => {
    const listed = client.readQuery<TaskRunListResp>({
      query: LIST_TASK_RUNS,
      variables: { limit: 100 },
    });
    return listed?.astroliftTaskRuns.find((r) => r.id === id) ?? null;
  }, [client, id]);

  const run = data?.astroliftTaskRun ?? cachedFromList;
  const live = run ? LIVE.has(run.status) : false;
  React.useEffect(() => {
    if (live) startPolling(5000);
    else stopPolling();
  }, [live, startPolling, stopPolling]);
  const now = useNow(live);

  return {
    loading,
    run,
    error: error && !run ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    now,
  };
}
