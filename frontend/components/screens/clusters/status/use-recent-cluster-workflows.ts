"use client";

import { useQuery } from "@apollo/client/react";

import { RECENT_CLUSTER_WORKFLOWS } from "@/graphql/clusters/clusters.queries";

import type { WorkflowRun } from "./types";

interface WorkflowsResp {
  astroliftRecentClusterWorkflows: WorkflowRun[];
}

/** Recent Temporal runs targeting one cluster (#394). */
export function useRecentClusterWorkflows(
  clusterId: string,
  { limit, pollInterval }: { limit: number; pollInterval?: number }
) {
  const { data, loading } = useQuery<WorkflowsResp>(RECENT_CLUSTER_WORKFLOWS, {
    variables: { clusterId, limit },
    pollInterval,
  });
  return { runs: data?.astroliftRecentClusterWorkflows ?? [], loading };
}
