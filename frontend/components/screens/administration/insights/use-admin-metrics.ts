"use client";

import { useQuery } from "@apollo/client/react";
import { useState } from "react";

import {
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
  CLUSTER_SYSTEM_METRICS,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";

import {
  WINDOWS,
  type MetricsCluster,
  type MetricsWindow,
  type PromInstant,
  type PromRange,
  type SystemMetrics,
  type WindowLabel,
} from "./metrics-format";

interface ClustersResp {
  astroliftClusters: MetricsCluster[];
}

interface PromInstantResp {
  astroliftClusterPrometheusMetrics: PromInstant;
}

interface PromRangeResp {
  astroliftClusterPrometheusRangeMetrics: PromRange;
}

interface SystemMetricsResp {
  astroliftClusterSystemMetrics: SystemMetrics;
}

/** The fleet: active clusters and the window every range query uses. */
export function useAdminMetrics() {
  const [windowLabel, setWindowLabel] = useState<WindowLabel>("1h");
  const win = WINDOWS.find((w) => w.label === windowLabel)!;

  const { data, loading, error, refetch } = useQuery<ClustersResp>(LIST_CLUSTERS, {
    pollInterval: 60_000,
  });
  const clusters = (data?.astroliftClusters ?? []).filter((c) => c.isActive);

  return {
    windowLabel,
    onWindowChange: setWindowLabel,
    win,
    clusters,
    loading,
    error: error ?? null,
    onRetry: (): void => {
      void refetch();
    },
  };
}

/**
 * One live cluster's in-cluster Prometheus: the instant snapshot (KPI
 * tiles) and the range series (sparklines). Mounted only for a cluster
 * whose heartbeat says it is reachable.
 */
export function usePrometheusPanel(clusterId: string, slug: string, win: MetricsWindow) {
  const { data: instantData, loading: instantLoading } = useQuery<PromInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    { variables: { clusterId }, pollInterval: 60_000 }
  );
  const { data: rangeData, loading: rangeLoading } = useQuery<PromRangeResp>(
    CLUSTER_PROMETHEUS_RANGE_METRICS,
    {
      variables: {
        clusterId,
        rangeSeconds: win.rangeSeconds,
        stepSeconds: win.stepSeconds,
      },
      pollInterval: 60_000,
    }
  );

  return {
    slug,
    instant: instantData?.astroliftClusterPrometheusMetrics ?? null,
    instantLoading,
    range: rangeData?.astroliftClusterPrometheusRangeMetrics ?? null,
    rangeLoading,
  };
}

/** One live cluster's cloud-provider system metrics (CloudWatch ALB on AWS). */
export function useSystemMetricsPanel(clusterId: string, providerSlug: string, win: MetricsWindow) {
  const { data, loading } = useQuery<SystemMetricsResp>(CLUSTER_SYSTEM_METRICS, {
    variables: {
      clusterId,
      rangeSeconds: win.rangeSeconds,
      stepSeconds: win.stepSeconds,
    },
    pollInterval: 60_000,
  });

  return {
    providerSlug,
    metrics: data?.astroliftClusterSystemMetrics ?? null,
    loading,
  };
}
