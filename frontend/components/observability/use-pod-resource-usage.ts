"use client";

import { useQuery } from "@apollo/client/react";

import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import type { AstroliftPodResourceUsage } from "@/graphql/__generated__/schema";
import { GET_POD_RESOURCE_USAGE } from "@/graphql/observability/observability.queries";

export type PodResourceUsage = AstroliftPodResourceUsage & { reason: ObservabilityPanelReason };

interface UsageResp {
  astroliftPodResourceUsage: PodResourceUsage | null;
}

const DEFAULT_RANGE_SECONDS = 60 * 60;

/**
 * One pod's CPU and memory over the last hour plus its restarts. Skipped
 * while no pod is expanded. The data half of PodExpander.
 */
export function usePodResourceUsage(
  appSlug: string,
  podName: string | null,
  environmentName: string | null
) {
  const q = useQuery<UsageResp>(GET_POD_RESOURCE_USAGE, {
    variables: {
      appSlug,
      podName: podName ?? "",
      environmentName,
      rangeSeconds: DEFAULT_RANGE_SECONDS,
    },
    skip: !podName,
    // Poll every 30s so an operator watching the panel sees the
    // sparkline tick along without manual refresh. 30s matches the
    // cadence kube-state-metrics scrapes by default.
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  return {
    usage: q.data?.astroliftPodResourceUsage ?? null,
    loading: q.loading && !q.data,
    onRetry: () => void q.refetch(),
  };
}
