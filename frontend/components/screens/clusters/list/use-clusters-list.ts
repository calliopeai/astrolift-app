"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  LIST_CLUSTERS,
  LIST_CLUSTERS_PAGE,
  REFRESH_CLUSTER_MANAGEMENT,
  UNREGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import type { ClusterHeartbeatFields } from "@/lib/cluster-heartbeat";

// The committed codegen output lags the live backend, so the generated
// AstroliftTenantCluster doesn't yet carry the heartbeat fields the
// cluster queries now select. Intersect them in locally.
export type ClusterRow = AstroliftTenantCluster & Partial<ClusterHeartbeatFields>;

interface ClustersPageResp {
  astroliftClustersPage: CursorPage<ClusterRow>;
}

// Active polling cadence while any row is in the "managing" state.
// 4 seconds keeps the UI responsive to the workflow (which typically
// completes in 10-30s) without hammering the apiserver. Drops back to
// the heartbeat cadence as soon as no visible row is managing.
const POLL_INTERVAL_MS = 4000;

// Steady-state poll cadence to keep heartbeat-derived live status pills
// fresh (#808). Matches the default agent heartbeat interval.
const HEARTBEAT_POLL_INTERVAL_MS = 30000;

// This surface walks `ListClustersPage`, but LIST_CLUSTERS still backs the
// cluster detail tabs, /ops, /providers, /administration/metrics and the
// fleet map. Both have to be refreshed after a lifecycle change or one of
// the two goes stale — the walk by operation name, since its variables
// carry the cursor and the search term and no literal variables object
// names the page the operator is actually looking at.
const REFETCH_LIST = [{ query: LIST_CLUSTERS }, "ListClustersPage"];

/**
 * The fleet walk and the lifecycle mutations behind /clusters. The data
 * half of ClustersList.
 */
export function useClustersList() {
  // `astroliftClustersPage` takes `search`, `limit` and `after` only —
  // there is no sort argument, so no column declares a `sortKey` and the
  // headers stay plain labels rather than controls that could only
  // reorder the page in hand (server-side sort is tracked in #1239).
  //
  // The controller's default `cache-and-network` is load-bearing here:
  // a cache-first read would answer from the SSR-primed result fetched
  // before the org cookie was set and never re-fetch, showing an empty
  // fleet to an org that has clusters.
  const table = useCursorTable<ClusterRow>({
    query: LIST_CLUSTERS_PAGE,
    extract: (d) => (d as ClustersPageResp | undefined)?.astroliftClustersPage,
    searchVariable: "search",
    urlKey: "cluster",
    // Steady-state cadence: keeps the heartbeat-derived Live pills fresh
    // without an operator reload (#808).
    pollInterval: HEARTBEAT_POLL_INTERVAL_MS,
  });

  // While a management workflow is in flight, overlay a faster refetch so
  // the lifecycle badge tracks the transition (it typically completes in
  // 10-30s). The cadence is decided by the page on screen rather than by
  // the whole fleet now that the walk is server-side — the fast poll
  // exists to animate a transition the operator is watching.
  const anyManaging = table.rows.some((c) => c.lifecycle === "managing");
  const { refetch } = table;
  React.useEffect(() => {
    if (!anyManaging) return;
    const id = setInterval(refetch, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [anyManaging, refetch]);

  const [unregister, { loading: deleting }] = useMutation<{
    unregisterTenantCluster: MutationResult<{ id: string; deleted: boolean }>;
  }>(UNREGISTER_TENANT_CLUSTER, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });
  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });

  // Throws on failure: ConfirmDialog keeps itself open and shows the error.
  async function onUnregister(c: AstroliftTenantCluster) {
    const { data } = await unregister({ variables: { input: { id: c.id } } });
    if (data?.unregisterTenantCluster.ok) {
      toast.success(`Unregistered ${c.slug}`);
    } else {
      throw new Error(data?.unregisterTenantCluster.errors?.[0]?.message ?? "Failed");
    }
  }

  async function onBring(c: AstroliftTenantCluster) {
    const { data } = await bring({ variables: { input: { clusterId: c.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${c.slug} into management — this can take up to a minute.`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function onRefresh(c: AstroliftTenantCluster, forcePreflight = false) {
    const { data } = await refresh({
      variables: { input: { clusterId: c.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight ? `Refreshing ${c.slug} (full preflight)` : `Refreshing ${c.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  return { table, deleting, bringing, refreshing, onUnregister, onBring, onRefresh };
}
