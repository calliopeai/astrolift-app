"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useListState } from "@/components/list/use-list-state";
import { LIST_CLUSTERS_PAGE } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { ClusterHeartbeatFields } from "@/lib/cluster-heartbeat";

import { CLUSTERS_LIST, clustersPageVariables } from "./clusters-list";
import { useClusterActions } from "./use-cluster-actions";

// The committed codegen output lags the live backend, so the generated
// AstroliftTenantCluster doesn't yet carry the heartbeat fields or who
// registered the cluster (#2150). Intersect them in locally.
export type ClusterRow = AstroliftTenantCluster &
  Partial<ClusterHeartbeatFields> & { createdByUsername?: string | null };

interface ClustersPageResp {
  astroliftClustersPage: {
    items: ClusterRow[];
    totalCount: number | null;
    page: number | null;
    pageSize: number | null;
  };
}

// Active polling cadence while any row is in the "managing" state.
// 4 seconds keeps the UI responsive to the workflow (which typically
// completes in 10-30s) without hammering the apiserver.
const POLL_INTERVAL_MS = 4000;

// Steady-state poll cadence to keep heartbeat-derived live status pills
// fresh (#808). Matches the default agent heartbeat interval.
const HEARTBEAT_POLL_INTERVAL_MS = 30000;

/**
 * The Clusters list: URL list state in, one numbered page of
 * `astroliftClustersPage` out (spec 44 §5.1, #2150), and the lifecycle
 * mutations. The server filters, sorts and counts; the data half of
 * ClustersList.
 *
 * `app/(app)/clusters/page.tsx` preloads the cold-load variables
 * (`clustersPageVariables` of the default list state); keep the two in step.
 */
export function useClustersList() {
  const list = useListState(CLUSTERS_LIST);
  const { state } = list;
  const variables = clustersPageVariables({
    q: state.q,
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  // `cache-and-network` is load-bearing: a cache-first read would answer
  // from the SSR-primed result fetched before the org cookie was set and
  // never re-fetch, showing an empty fleet to an org that has clusters.
  const query = useQuery<ClustersPageResp>(LIST_CLUSTERS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: HEARTBEAT_POLL_INTERVAL_MS,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftClustersPage;
  const rows = page?.items ?? [];
  const { refetch } = query;

  // While a management workflow is in flight, overlay a faster refetch so
  // the status badge tracks the transition.
  const anyManaging = rows.some((c) => c.lifecycle === "managing");
  React.useEffect(() => {
    if (!anyManaging) return;
    const id = setInterval(() => void refetch(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [anyManaging, refetch]);

  return {
    list,
    rows,
    totalCount: page?.totalCount ?? rows.length,
    loading: query.loading && !data,
    // Rows on screen answer the previous list state while the next loads.
    stale: query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => void refetch(),
    registerHref: "/clusters/new",
    ...useClusterActions(),
  };
}
