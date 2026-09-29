"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { LIST_CLUSTERS_PAGE } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";
import type { ClusterHeartbeatFields } from "@/lib/cluster-heartbeat";

import { CLUSTERS_LIST, selectClusters } from "./clusters-list";
import { useClusterActions } from "./use-cluster-actions";

// The committed codegen output lags the live backend, so the generated
// AstroliftTenantCluster doesn't yet carry the heartbeat fields the
// cluster queries now select. Intersect them in locally.
export type ClusterRow = AstroliftTenantCluster & Partial<ClusterHeartbeatFields>;

interface ClustersPageResp {
  astroliftClustersPage: CursorPage<ClusterRow>;
}

/**
 * The walk's page size: the backend's MAX_PAGE_LIMIT, so a fleet of up to
 * 200 is one request. `app/(app)/clusters/page.tsx` preloads exactly
 * `{ search: null, limit: WALK_LIMIT }`; keep the two in step.
 */
const WALK_LIMIT = 200;

// Active polling cadence while any row is in the "managing" state.
// 4 seconds keeps the UI responsive to the workflow (which typically
// completes in 10-30s) without hammering the apiserver.
const POLL_INTERVAL_MS = 4000;

// Steady-state poll cadence to keep heartbeat-derived live status pills
// fresh (#808). Matches the default agent heartbeat interval.
const HEARTBEAT_POLL_INTERVAL_MS = 30000;

const NO_ROWS: ClusterRow[] = [];

/**
 * Every cluster matching `search`: the first page through `useQuery` (so the
 * SSR preload and the poll apply), the rest, when a fleet passes one page,
 * walked by cursor. Nothing past the first page is ever dropped (#1230).
 */
function useFleet(search: string | null) {
  const client = useApolloClient();
  // `cache-and-network` is load-bearing: a cache-first read would answer
  // from the SSR-primed result fetched before the org cookie was set and
  // never re-fetch, showing an empty fleet to an org that has clusters.
  const head = useQuery<ClustersPageResp>(LIST_CLUSTERS_PAGE, {
    variables: { search, limit: WALK_LIMIT },
    fetchPolicy: "cache-and-network",
    pollInterval: HEARTBEAT_POLL_INTERVAL_MS,
  });
  const data = head.data ?? head.previousData;
  const first = data?.astroliftClustersPage;
  const cursor = first?.nextCursor ?? null;

  const [tail, setTail] = React.useState<{ after: string; rows: ClusterRow[] } | null>(null);
  const [tailError, setTailError] = React.useState<Error | null>(null);

  React.useEffect(() => {
    if (!cursor) return;
    let cancelled = false;
    (async () => {
      const rows: ClusterRow[] = [];
      let after: string | null = cursor;
      while (after && !cancelled) {
        const res: { data?: ClustersPageResp } = await client.query<ClustersPageResp>({
          query: LIST_CLUSTERS_PAGE,
          variables: { search, limit: WALK_LIMIT, after },
          fetchPolicy: "network-only",
        });
        const page = res.data?.astroliftClustersPage;
        rows.push(...(page?.items ?? []));
        after = page?.nextCursor ?? null;
      }
      if (!cancelled) {
        setTail({ after: cursor, rows });
        setTailError(null);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setTailError(e instanceof Error ? e : new Error(String(e)));
    });
    return () => {
      cancelled = true;
    };
    // `first` changes on every poll; re-walk so the tail stays as fresh as the head.
  }, [client, cursor, search, first]);

  const tailRows = cursor && tail?.after === cursor ? tail.rows : NO_ROWS;
  const fleet = React.useMemo(
    () => [...(first?.items ?? []), ...tailRows],
    [first?.items, tailRows]
  );

  return {
    fleet,
    loading: head.loading && !data,
    // Rows on screen answer an older search, or the tail is still walking.
    stale:
      (head.loading && !head.data && Boolean(data)) || Boolean(cursor && tail?.after !== cursor),
    error: head.error ?? tailError,
    refetch: head.refetch,
  };
}

/**
 * The Clusters list: URL list state, the fleet walk, and the lifecycle
 * mutations. The data half of ClustersList.
 */
export function useClustersList() {
  const list = useListState(CLUSTERS_LIST);
  const { state } = list;
  const search = state.q.trim() || null;
  const { fleet, loading, stale, error, refetch } = useFleet(search);

  const me = useQuery<MeQueryData>(GET_ME).data?.me?.profile?.username ?? null;

  const { rows, totalCount } = selectClusters(fleet, {
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
    me,
  });

  // While a management workflow is in flight, overlay a faster refetch so
  // the status badge tracks the transition. Decided by the fleet, not the
  // page on screen: a row filtered out of view is still mid-transition.
  const anyManaging = fleet.some((c) => c.lifecycle === "managing");
  React.useEffect(() => {
    if (!anyManaging) return;
    const id = setInterval(() => void refetch(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [anyManaging, refetch]);

  return {
    list,
    rows,
    totalCount,
    loading,
    stale,
    error: error ? { message: error.message } : null,
    onRetry: () => void refetch(),
    registerHref: "/clusters/new",
    ...useClusterActions(),
  };
}
