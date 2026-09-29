"use client";

import { useQuery } from "@apollo/client/react";

import { CLUSTER_APP_COUNT, CLUSTER_PROMETHEUS_METRICS } from "@/graphql/clusters/clusters.queries";

interface AppCountResp {
  astroliftAppCountForCluster: number;
}

export interface ClusterPrometheusInstant {
  available: boolean;
  nodeCount: number | null;
  podRunningRatio: number | null;
  cpuUtilization: number | null;
  memoryUtilization: number | null;
  deploymentReadyRatio: number | null;
}

interface PrometheusInstantResp {
  astroliftClusterPrometheusMetrics: ClusterPrometheusInstant;
}

/**
 * Apps bound (from the cluster query) plus Prometheus instant metrics.
 * The data half of ClusterLiveStats.
 */
export function useClusterLiveStats(clusterId: string) {
  const { data: appData, loading: appLoading } = useQuery<AppCountResp>(CLUSTER_APP_COUNT, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  const { data: promData, loading: promLoading } = useQuery<PrometheusInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    {
      variables: { clusterId },
      pollInterval: 60000,
    }
  );

  return {
    appCount: appData?.astroliftAppCountForCluster,
    appLoading,
    metrics: promData?.astroliftClusterPrometheusMetrics,
    metricsLoading: promLoading && !promData,
  };
}
