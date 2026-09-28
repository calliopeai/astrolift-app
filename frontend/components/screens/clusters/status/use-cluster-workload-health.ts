"use client";

import { useQuery } from "@apollo/client/react";

import { CLUSTER_WORKLOAD_HEALTH } from "@/graphql/clusters/clusters.queries";

import type { WorkloadRow } from "./types";

interface WorkloadHealthResp {
  astroliftClusterWorkloadHealth: WorkloadRow[];
}

/** Per-Deployment readiness + 24h restarts (#362), polled every 30s. */
export function useClusterWorkloadHealth(clusterId: string) {
  const { data, loading } = useQuery<WorkloadHealthResp>(CLUSTER_WORKLOAD_HEALTH, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  return { rows: data?.astroliftClusterWorkloadHealth ?? [], loading };
}
