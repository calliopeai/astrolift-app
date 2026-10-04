"use client";

import { useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";

import type { CursorPage } from "@/components/data-table";
import type { ListStateController } from "@/components/list/list-state";

import { numberedVariables } from "./identity-lists";

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

/**
 * Binds a list's state to an identity `…Page` query on the list contract
 * (#2153): search, filter, sort and the page number all go to the server,
 * which answers one numbered page and the exact `totalCount`. `toFilter`
 * turns the list's filters (views and chips) into the query's `filter`.
 */
export function useNumberedListQuery<TRow, F extends object>(
  query: DocumentNode,
  list: ListStateController,
  extract: (data: unknown) => CursorPage<TRow> | null | undefined,
  {
    toFilter,
    variables = {},
    skip = false,
    freshOnly = false,
  }: {
    toFilter: (filters: Record<string, string>) => F;
    variables?: Record<string, unknown>;
    skip?: boolean;
    /** Scope-keyed callers require a fresh response instead of shared cache data. */
    freshOnly?: boolean;
  }
): ListPageData<TRow> {
  const { state } = list;
  const result = useQuery(query, {
    variables: { ...variables, ...numberedVariables(state, toFilter(list.filters)) },
    skip,
    fetchPolicy: freshOnly ? "no-cache" : "cache-and-network",
    context: freshOnly ? { queryDeduplication: false } : undefined,
  });
  const fresh = extract(result.data);
  const page =
    freshOnly && (skip || result.loading || result.error)
      ? null
      : (fresh ?? (!freshOnly ? extract(result.previousData) : null));
  return {
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? null,
    nextCursor: null,
    loading: !skip && result.loading && !page,
    stale: result.loading && !fresh && Boolean(page),
    error: result.error ? { message: result.error.message } : null,
    refetch: () => {
      void result.refetch();
    },
  };
}
