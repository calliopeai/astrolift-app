"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import { GET_TASK_RUN, LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

interface TaskRunResp {
  astroliftTaskRun: AstroliftTaskRun | null;
}

interface TaskRunListResp {
  astroliftTaskRuns: AstroliftTaskRun[];
}

/**
 * Task run by id (#1106, #1118). Prefers the singular `astroliftTaskRun(id)`
 * query so a cold deep-link to a run outside the 100-row list window still
 * resolves. Falls back to the row already in the cached LIST_TASK_RUNS window
 * for an instant paint when navigated from /tasks (cache hit); the by-id fetch
 * refreshes in the background and is the source of truth otherwise. The data
 * half of TaskRunDetail.
 */
export function useTaskRunDetail(id: string) {
  const client = useApolloClient();
  const { data, loading } = useQuery<TaskRunResp>(GET_TASK_RUN, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });

  const cachedFromList = React.useMemo(() => {
    const listed = client.readQuery<TaskRunListResp>({
      query: LIST_TASK_RUNS,
      variables: { limit: 100 },
    });
    return listed?.astroliftTaskRuns.find((r) => r.id === id) ?? null;
  }, [client, id]);

  return { loading, run: data?.astroliftTaskRun ?? cachedFromList };
}
