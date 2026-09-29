"use client";

import type { DocumentNode } from "@apollo/client";
import { useQuery } from "@apollo/client/react";

import type { CursorPage } from "@/components/data-table";
import type { ListStateController } from "@/components/list/list-state";

/**
 * One cursor page of an app-scoped `…Page` field for a ListPage embedded in
 * an app tab: the list's search, page size and cursor become the query's
 * `search`, `limit` and `after` (spec 44 §5.1). The fields these lists read
 * take no filter and no sort, so their definitions declare none; the rest of
 * the ListPage props come back ready to spread.
 */
export function useCursorList<T>({
  query,
  variables,
  extract,
  list,
  pollInterval,
}: {
  query: DocumentNode;
  /** The scope (`appSlug`, `workloadSlug`), the same on every page. */
  variables: Record<string, unknown>;
  extract: (data: unknown) => CursorPage<T> | undefined;
  list: ListStateController;
  pollInterval?: number;
}) {
  const { state } = list;
  const q = useQuery(query, {
    variables: {
      ...variables,
      search: state.q.trim() || null,
      limit: state.pageSize,
      after: state.after,
    },
    fetchPolicy: "cache-and-network",
    pollInterval,
  });
  const data = q.data ?? q.previousData;
  const page = extract(data);
  return {
    list,
    rows: page?.items ?? [],
    loading: q.loading && !data,
    /** Rows on screen answer the previous question while the next loads. */
    stale: q.loading && !q.data && Boolean(data),
    error: q.error && !data ? { message: q.error.message } : null,
    onRetry: () => {
      void q.refetch();
    },
    nextCursor: page?.nextCursor ?? null,
    totalCount: page?.totalCount ?? null,
  };
}
