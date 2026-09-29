"use client";

import { useQuery } from "@apollo/client/react";

import { GET_WORKLOAD_RESOURCE_USAGE } from "@/graphql/observability/observability.queries";

// 5s poll matches the issue spec — short enough to feel live, long
// enough to ride on the upstream prometheus_client TTL cache so we
// don't hammer Prometheus.
const RESOURCE_POLL_MS = 5_000;

export interface ResourceGauge {
  unit: string;
  current: number;
  request: number;
  limit: number;
  percentOfRequest: number;
  percentOfLimit: number;
}

export interface ResourceUsage {
  cpu: ResourceGauge;
  memory: ResourceGauge;
  sourcedAt: string;
}

interface ResourceUsageResp {
  astroliftWorkloadResourceUsage: ResourceUsage | null;
}

/** Live CPU / memory usage for one workload. The data half of ResourceUsageGaugesView. */
export function useWorkloadResourceUsage(
  appSlug: string,
  workloadSlug: string,
  environmentName: string | null
) {
  const { data, loading } = useQuery<ResourceUsageResp>(GET_WORKLOAD_RESOURCE_USAGE, {
    variables: { appSlug, workloadSlug, environmentName },
    pollInterval: RESOURCE_POLL_MS,
    fetchPolicy: "cache-and-network",
  });
  return { usage: data?.astroliftWorkloadResourceUsage ?? null, loading };
}
