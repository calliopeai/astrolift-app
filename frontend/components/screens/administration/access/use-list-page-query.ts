"use client";

import { useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";

import type { CursorPage } from "@/components/data-table";
import type { ListStateController } from "@/components/list/use-list-state";

/** What a ListPage needs from one cursor-paged query. */
export interface ListPageData<TRow> {
  rows: TRow[];
  totalCount: number | null;
  nextCursor: string | null;
  /** First load, nothing to show yet. */
  loading: boolean;
  /** Rows on screen answer the previous question while the next one loads. */
  stale: boolean;
  error: { message: string } | null;
  refetch: () => void;
}

/**
 * Binds a list's state to one of the identity `…Page` queries, which speak
 * `search` + `limit` + `after` and return `{ items, nextCursor, totalCount }`.
 * They take no filter or sort argument yet, so only the search and the page
 * reach the server; a list built on this declares no filter fields and no
 * sortable columns (spec 44 §5.1's contract is the backend's to add).
 *
 * The search arrives already debounced (FilterBar owns that), and every change
 * to it resets `after`, so a cursor is never replayed against a new question.
 */
export function useListPageQuery<TRow>(
  query: DocumentNode,
  list: ListStateController,
  extract: (data: unknown) => CursorPage<TRow> | null | undefined,
  { variables = {}, skip = false }: { variables?: Record<string, unknown>; skip?: boolean } = {}
): ListPageData<TRow> {
  const { state } = list;
  const result = useQuery(query, {
    variables: {
      ...variables,
      search: state.q.trim() || null,
      limit: state.pageSize,
      after: state.after,
    },
    skip,
    fetchPolicy: "cache-and-network",
  });
  const fresh = extract(result.data);
  const page = fresh ?? extract(result.previousData);
  return {
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? null,
    nextCursor: page?.nextCursor ?? null,
    loading: !skip && result.loading && !page,
    stale: result.loading && !fresh && Boolean(page),
    error: result.error ? { message: result.error.message } : null,
    refetch: () => {
      void result.refetch();
    },
  };
}
