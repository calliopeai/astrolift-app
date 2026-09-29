"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_EVENTS_PAGE } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

interface EventsPageResp {
  astroliftEventsPage: { items: AstroliftEvent[]; nextCursor?: string | null };
}

/** One page of the feed; older pages load as the reader nears the end. */
export const ACTIVITY_PAGE_SIZE = 25;

/**
 * Recent platform events for one app, newest first, on the events cursor
 * (Leo's list rule 5): the first page, then older pages merged in as the
 * reader nears the end of the feed's frame. Server-side filtered by appSlug
 * so only this app's events load (the empty-feed bug came from comparing
 * the app GUID against the integer PK in the client). The data half of
 * ActivityTimelineView.
 */
export function useActivityTimeline(appSlug: string) {
  const { data, loading, error, refetch, fetchMore } = useQuery<EventsPageResp>(LIST_EVENTS_PAGE, {
    variables: { appSlug, limit: ACTIVITY_PAGE_SIZE },
    fetchPolicy: "cache-and-network",
  });
  const page = data?.astroliftEventsPage;
  const nextCursor = page?.nextCursor ?? null;
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [moreError, setMoreError] = React.useState<string | null>(null);

  const onLoadMore = React.useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      await fetchMore({
        variables: { appSlug, limit: ACTIVITY_PAGE_SIZE, after: nextCursor },
        updateQuery: (prev, { fetchMoreResult }) => {
          if (!fetchMoreResult) return prev;
          return {
            astroliftEventsPage: {
              ...fetchMoreResult.astroliftEventsPage,
              items: [
                ...(prev.astroliftEventsPage?.items ?? []),
                ...fetchMoreResult.astroliftEventsPage.items,
              ],
            },
          } as EventsPageResp;
        },
      });
    } catch (e) {
      setMoreError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [appSlug, fetchMore, loadingMore, nextCursor]);

  return {
    events: page?.items ?? [],
    loading: loading && !data,
    /** With nothing cached it takes the feed; with events it sits at the end. */
    error: data ? moreError : (error?.message ?? null),
    onRetry: () => void refetch(),
    hasMore: Boolean(nextCursor),
    loadingMore,
    onLoadMore: () => void onLoadMore(),
  };
}
