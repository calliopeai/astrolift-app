"use client";

import { useQuery } from "@apollo/client/react";

import { useGrowingLimit } from "@/components/feed/use-growing-limit";
import { RECENT_CLUSTER_WORKFLOWS } from "@/graphql/clusters/clusters.queries";

import type { WorkflowRun } from "./types";

interface WorkflowsResp {
  astroliftRecentClusterWorkflows: WorkflowRun[];
}

/**
 * Recent Temporal runs targeting one cluster (#394). `limit` is the first
 * window; the Activity tab's feed asks for more as the reader nears the
 * end (the field takes a limit, not a cursor).
 */
export function useRecentClusterWorkflows(
  clusterId: string,
  { limit, pollInterval = 15000 }: { limit: number; pollInterval?: number }
) {
  const grow = useGrowingLimit(limit);
  const { data, loading, error, refetch } = useQuery<WorkflowsResp>(RECENT_CLUSTER_WORKFLOWS, {
    variables: { clusterId, limit: grow.limit },
    pollInterval,
    fetchPolicy: "cache-and-network",
  });
  const runs = data?.astroliftRecentClusterWorkflows ?? [];
  return {
    runs,
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
    more: grow.feed(runs.length, loading),
  };
}
