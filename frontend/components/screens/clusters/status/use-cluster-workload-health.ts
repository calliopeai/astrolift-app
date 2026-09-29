"use client";

import { useQuery } from "@apollo/client/react";

import { selectRows } from "@/components/list/select-rows";
import { useListState } from "@/components/list/use-list-state";
import { CLUSTER_WORKLOAD_HEALTH } from "@/graphql/clusters/clusters.queries";

import { CLUSTER_WORKLOADS_LIST, CLUSTER_WORKLOADS_SELECT } from "./cluster-workloads-list";
import type { WorkloadRow } from "./types";

interface WorkloadHealthResp {
  astroliftClusterWorkloadHealth: WorkloadRow[];
}

/** Per-Deployment readiness + 24h restarts (#362), polled every 30s. */
export function useClusterWorkloadHealth(clusterId: string) {
  const { data, loading, error, refetch } = useQuery<WorkloadHealthResp>(CLUSTER_WORKLOAD_HEALTH, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  return {
    rows: data?.astroliftClusterWorkloadHealth ?? [],
    loading,
    error: error?.message ?? null,
    refetch: () => {
      void refetch();
    },
  };
}

/**
 * The Health tab's workload list: the same query as above, filtered,
 * sorted and paged in the client over URL list state (the field has no
 * arguments yet; see cluster-workloads-list.ts).
 */
export function useClusterWorkloadList(clusterId: string) {
  const list = useListState(CLUSTER_WORKLOADS_LIST);
  const workloads = useClusterWorkloadHealth(clusterId);
  const { state } = list;
  const { rows, totalCount } = selectRows(
    workloads.rows,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    CLUSTER_WORKLOADS_SELECT
  );
  return {
    list,
    rows,
    totalCount,
    loading: workloads.loading && workloads.rows.length === 0,
    error: workloads.error && workloads.rows.length === 0 ? { message: workloads.error } : null,
    onRetry: workloads.refetch,
  };
}
