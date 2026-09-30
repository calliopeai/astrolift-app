"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

/**
 * The task's runs behind TaskHomeScreen, newest first. The server filters the
 * app and workload before taking the newest thirty.
 */
export function useTaskRuns(appSlug: string, workloadSlug: string) {
  const { data, loading, error, refetch } = useQuery<{ astroliftTaskRuns: AstroliftTaskRun[] }>(
    LIST_TASK_RUNS,
    {
      variables: { appSlug, workloadSlug, limit: 30 },
      fetchPolicy: "cache-and-network",
      pollInterval: 15000,
    }
  );
  const runs = data?.astroliftTaskRuns ?? [];
  return {
    runs,
    loading: loading && !data,
    error: data ? null : (error ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
