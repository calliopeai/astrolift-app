"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { GET_RECENT_ACTIVITY } from "@/graphql/operations/operations.queries";
import type { AstroliftActivityPage } from "@/graphql/operations/operations.types";

interface RecentActivityResp {
  astroliftRecentActivity: AstroliftActivityPage;
}

/** The recent activity page, cursor-paged with load more: the data half of ActivityFeed. */
export function useRecentActivity(pageSize = 10) {
  const { data, loading, error, fetchMore, refetch } = useQuery<RecentActivityResp>(
    GET_RECENT_ACTIVITY,
    {
      variables: { limit: pageSize },
      fetchPolicy: "cache-and-network",
    }
  );

  const page = data?.astroliftRecentActivity;
  const items = page?.items ?? [];
  const nextCursor = page?.nextCursor ?? null;
  const [loadingMore, setLoadingMore] = React.useState(false);

  const handleLoadMore = React.useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      await fetchMore({
        variables: { limit: pageSize, cursor: nextCursor },
        // Merge page-by-page so the rendered list grows; nextCursor
        // always reflects the *latest* fetch so a third click works.
        updateQuery: (prev, { fetchMoreResult }) => {
          if (!fetchMoreResult) return prev;
          return {
            astroliftRecentActivity: {
              ...fetchMoreResult.astroliftRecentActivity,
              items: [
                ...prev.astroliftRecentActivity.items,
                ...fetchMoreResult.astroliftRecentActivity.items,
              ],
            },
          };
        },
      });
    } finally {
      setLoadingMore(false);
    }
  }, [fetchMore, loadingMore, nextCursor, pageSize]);

  return {
    items,
    loading,
    error: error?.message ?? null,
    hasMore: Boolean(nextCursor),
    loadingMore,
    onLoadMore: handleLoadMore,
    onRetry: () => void refetch(),
  };
}
