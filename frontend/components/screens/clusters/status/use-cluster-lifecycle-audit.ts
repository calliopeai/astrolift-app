"use client";

import { useQuery } from "@apollo/client/react";

import { CLUSTER_LIFECYCLE_AUDIT } from "@/graphql/clusters/clusters.queries";

import type { AuditRow } from "./types";

interface LifecycleResp {
  astroliftClusterLifecycleAudit: AuditRow[];
}

/** Audit-log mutations that targeted one cluster (#68 slice 2). */
export function useClusterLifecycleAudit(
  clusterId: string,
  { limit, pollInterval }: { limit: number; pollInterval?: number }
) {
  const { data, loading, error, refetch } = useQuery<LifecycleResp>(CLUSTER_LIFECYCLE_AUDIT, {
    variables: { clusterId, limit },
    pollInterval,
  });
  return {
    entries: data?.astroliftClusterLifecycleAudit ?? [],
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
  };
}
