"use client";

import { type OperationVariables, type TypedDocumentNode } from "@apollo/client";
import { useApolloClient, useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";
import * as React from "react";

import type { CursorPage } from "@/components/data-table";
import { useHeldRows } from "@/components/list/use-held-rows";

/**
 * The data half of a `Feed` over one cursor-paged field (Leo's list rule 5):
 *
 *   const feed = useCursorFeed(LIST_EVENTS_PAGE, {
 *     variables: { search: q || null },
 *     select: (d) => d.astroliftEventsPage,
 *     keyOf: (e) => e.id,
 *     pollInterval: 15000,
 *   });
 *   <Feed label="Events" {...feed.feed} keyOf={(e) => e.id} renderItem={…} />
 *
 * The newest page is an ordinary query (so it polls, and a mutation's
 * refetch by name reaches it); older pages are fetched once each as the
 * reader nears the end and kept beside it, so a poll never throws away what
 * the reader scrolled to. Items that arrive on the newest page wait behind
 * the "n new" pill. Changing the variables (a search, a filter) starts over.
 */
export interface CursorFeedOptions<TData, TItem, TVars extends OperationVariables> {
  /** Everything but the page size and the cursor. */
  variables?: Omit<TVars, "limit" | "after">;
  select: (data: TData | undefined) => CursorPage<TItem> | null | undefined;
  keyOf: (item: TItem) => string;
  pageSize?: number;
  /** The cursor argument's name; most fields call it `after`. */
  cursorVar?: string;
  /** The page size argument's name; most fields call it `limit`. */
  limitVar?: string;
  pollInterval?: number;
  skip?: boolean;
  /** Hold new items behind the pill. Off for feeds that only grow at the end. */
  live?: boolean;
}

export function useCursorFeed<TData, TItem, TVars extends OperationVariables = OperationVariables>(
  query: DocumentNode | TypedDocumentNode<TData, TVars>,
  {
    variables,
    select,
    keyOf,
    pageSize = 25,
    cursorVar = "after",
    limitVar = "limit",
    pollInterval,
    skip = false,
    live = true,
  }: CursorFeedOptions<TData, TItem, TVars>
) {
  const client = useApolloClient();
  const vars = { ...(variables ?? {}), [limitVar]: pageSize } as unknown as TVars;
  const resetKey = JSON.stringify(vars);

  const head = useQuery<TData, TVars>(query, {
    variables: vars,
    fetchPolicy: "cache-and-network",
    pollInterval,
    skip,
  } as never);
  const data = (head.data ?? head.previousData) as TData | undefined;
  const first = select(data);

  const [older, setOlder] = React.useState<{
    key: string;
    items: TItem[];
    cursor: string | null;
    error: string | null;
  } | null>(null);
  const [loadingMore, setLoadingMore] = React.useState(false);
  // A new question drops the older pages of the last one.
  const tail = older?.key === resetKey ? older : null;

  const cursor = tail ? tail.cursor : (first?.nextCursor ?? null);

  const onLoadMore = React.useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await client.query<TData, TVars>({
        query,
        variables: { ...vars, [cursorVar]: cursor } as TVars,
        fetchPolicy: "network-only",
      } as never);
      const page = select(res.data as TData | undefined);
      setOlder({
        key: resetKey,
        items: [...(tail?.items ?? []), ...(page?.items ?? [])],
        cursor: page?.nextCursor ?? null,
        error: null,
      });
    } catch (err) {
      setOlder({
        key: resetKey,
        items: tail?.items ?? [],
        cursor,
        error: err instanceof Error ? err.message : "Could not load older items",
      });
    } finally {
      setLoadingMore(false);
    }
    // `vars` is rebuilt every render; `resetKey` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, query, cursor, cursorVar, loadingMore, resetKey, select, tail]);

  // New items only ever arrive on the newest page, so only it is held.
  const held = useHeldRows(first?.items ?? [], keyOf, { live, resetKey });

  // The newest page first, then the older ones, each item once.
  const items = React.useMemo(() => {
    const seen = new Set<string>();
    const out: TItem[] = [];
    for (const item of [...held.rows, ...(tail?.items ?? [])]) {
      const k = keyOf(item);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(item);
    }
    return out;
  }, [held.rows, tail, keyOf]);

  const headError = head.error && !data ? head.error.message : null;

  return {
    /** Spread onto `<Feed>`. */
    feed: {
      items,
      loading: head.loading && !data && !skip,
      error: headError ?? tail?.error ?? null,
      onRetry: () => {
        if (headError) void head.refetch();
        else void onLoadMore();
      },
      hasMore: Boolean(cursor),
      loadingMore,
      onLoadMore: () => void onLoadMore(),
      newCount: held.newCount,
      onShowNew: held.reveal,
    },
    /** The server's count of the whole feed, where the field reports one. */
    totalCount: first?.totalCount ?? null,
    refetch: () => head.refetch(),
  };
}
