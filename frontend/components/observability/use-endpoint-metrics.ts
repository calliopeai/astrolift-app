"use client";

import { useQuery } from "@apollo/client/react";

import type { AstroliftAppEndpointMetric } from "@/graphql/__generated__/schema";
import { GET_APP_ENDPOINT_METRICS } from "@/graphql/observability/observability.queries";

interface EndpointMetricsResp {
  astroliftAppEndpointMetrics: AstroliftAppEndpointMetric[];
}

export interface EndpointMetricsPanelProps {
  appSlug: string;
  environmentName?: string | null;
  workloadSlug?: string | null;
  rangeSeconds?: number;
}

const DEFAULT_RANGE_SECONDS = 3600;

/** Per-endpoint HTTP metrics (#641), polled every 30s: the data half of EndpointMetricsPanel. */
export function useEndpointMetrics({
  appSlug,
  environmentName,
  workloadSlug,
  rangeSeconds = DEFAULT_RANGE_SECONDS,
}: EndpointMetricsPanelProps) {
  const { data, loading } = useQuery<EndpointMetricsResp>(GET_APP_ENDPOINT_METRICS, {
    variables: {
      appSlug,
      environmentName: environmentName ?? null,
      workloadSlug: workloadSlug ?? null,
      rangeSeconds,
    },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });
  return { metrics: data?.astroliftAppEndpointMetrics ?? [], loading };
}
