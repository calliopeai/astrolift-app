"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";

/**
 * The task's runs behind TaskHomeScreen, newest first. The query is
 * app-scoped, so this workload's runs are kept here.
 */
export function useTaskRuns(appSlug: string, workloadSlug: string) {
  const { data, loading, error, refetch } = useQuery<{ astroliftTaskRuns: AstroliftTaskRun[] }>(
    LIST_TASK_RUNS,
    {
      variables: { appSlug, limit: 30 },
      fetchPolicy: "cache-and-network",
      pollInterval: 15000,
    }
  );
  const runs = (data?.astroliftTaskRuns ?? []).filter((r) => r.workloadSlug === workloadSlug);
  return {
    runs,
    loading: loading && !data,
    error: data ? null : (error ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
