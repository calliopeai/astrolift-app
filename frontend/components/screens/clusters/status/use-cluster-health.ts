"use client";

import { useQuery } from "@apollo/client/react";

import { useGrowingLimit } from "@/components/feed/use-growing-limit";
import { CLUSTER_HEALTH } from "@/graphql/clusters/clusters.queries";

import type { ClusterHealthPayload } from "./types";

interface HealthResp {
  astroliftClusterHealth: ClusterHealthPayload | null;
}

/**
 * Pod-phase rollup + recent Warning events from the driver, polled every
 * 30s. `eventLimit` is the first window of events; the Health tab's feed
 * asks for more as the reader nears the end.
 */
export function useClusterHealth(clusterId: string, eventLimit: number) {
  const grow = useGrowingLimit(eventLimit);
  const { data, loading, error, refetch } = useQuery<HealthResp>(CLUSTER_HEALTH, {
    variables: { clusterId, eventLimit: grow.limit },
    pollInterval: 30000,
  });
  const payload = data?.astroliftClusterHealth;
  const events = payload?.events ?? [];
  return {
    pods: payload?.pods ?? [],
    events,
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
    moreEvents: grow.feed(events.length, loading),
  };
}
