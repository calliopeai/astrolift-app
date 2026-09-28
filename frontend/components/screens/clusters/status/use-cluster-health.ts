"use client";

import { useQuery } from "@apollo/client/react";

import { CLUSTER_HEALTH } from "@/graphql/clusters/clusters.queries";

import type { ClusterHealthPayload } from "./types";

interface HealthResp {
  astroliftClusterHealth: ClusterHealthPayload | null;
}

/** Pod-phase rollup + recent Warning events from the driver, polled every 30s. */
export function useClusterHealth(clusterId: string, eventLimit: number) {
  const { data, loading, error, refetch } = useQuery<HealthResp>(CLUSTER_HEALTH, {
    variables: { clusterId, eventLimit },
    pollInterval: 30000,
  });
  const payload = data?.astroliftClusterHealth;
  return {
    pods: payload?.pods ?? [],
    events: payload?.events ?? [],
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
  };
}
