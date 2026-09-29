"use client";

import { useQuery } from "@apollo/client/react";

import { useGrowingLimit } from "@/components/feed/use-growing-limit";
import { CLUSTER_LIFECYCLE_AUDIT } from "@/graphql/clusters/clusters.queries";

import type { AuditRow } from "./types";

interface LifecycleResp {
  astroliftClusterLifecycleAudit: AuditRow[];
}

/**
 * Audit-log mutations that targeted one cluster (#68 slice 2). `limit` is
 * the first window; the Activity tab's feed asks for more as the reader
 * nears the end (the field takes a limit, not a cursor).
 */
export function useClusterLifecycleAudit(
  clusterId: string,
  { limit, pollInterval }: { limit: number; pollInterval?: number }
) {
  const grow = useGrowingLimit(limit);
  const { data, loading, error, refetch } = useQuery<LifecycleResp>(CLUSTER_LIFECYCLE_AUDIT, {
    variables: { clusterId, limit: grow.limit },
    pollInterval,
  });
  const entries = data?.astroliftClusterLifecycleAudit ?? [];
  return {
    entries,
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
    more: grow.feed(entries.length, loading),
  };
}
