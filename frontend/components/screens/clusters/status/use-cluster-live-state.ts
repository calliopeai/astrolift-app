"use client";

import { useQuery } from "@apollo/client/react";

import { CLUSTER_LIVE_STATE } from "@/graphql/clusters/clusters.queries";
import type { ClusterLiveState } from "@/lib/cluster-heartbeat";

interface LiveStateResp {
  astroliftClusterLiveState: ClusterLiveState | null;
}

/**
 * The persisted keep-alive heartbeat (#808). The one status read that
 * makes no driver / Prometheus / Temporal call, so it is safe to poll
 * while the cluster is offline.
 */
export function useClusterLiveState(clusterId: string) {
  const { data, loading, error, refetch } = useQuery<LiveStateResp>(CLUSTER_LIVE_STATE, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  return {
    state: data?.astroliftClusterLiveState ?? null,
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
  };
}
