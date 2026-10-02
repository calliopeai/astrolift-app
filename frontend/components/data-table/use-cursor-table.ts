"use client";

/**
 * useCursorTable — the one data controller behind every table (#1231).
 *
 * There is deliberately no client-side counterpart. The previous standard
 * shipped both and every surface picked the client one, which is how
 * `/deployments` ended up fetching `limit: 100` and paginating it locally:
 * deployment 101 did not exist as far as the UI was concerned. Paging on
 * the server is the only mode that is correct for a list whose length the
 * client does not control.
 *
 * The single sanctioned exception is a small fixed-cardinality list (the
 * ~17 system roles, say), which may render without pagination — but still
 * through `DataTable`, for the same chrome — and the exception has to be
 * argued in the PR rather than assumed.
 *
 * Speaks the platform's own dialect: `{ items, nextCursor, totalCount }`
 * out, `limit` + `after` in. The abstraction it replaces required
 * `first`/`offset`/`edges`/`totalCount`, a shape exactly one query in the
 * whole schema spoke — the dead `employees` scaffold — which is why it sat
 * unused for three months across 100+ new surfaces.
 */

import * as React from "react";
import { useQuery } from "@apollo/client/react";
import type { DocumentNode } from "graphql";
import type { ErrorLike, OperationVariables, WatchQueryFetchPolicy } from "@apollo/client";

import { useDebounce } from "@/hooks/use-debounce";

import type { CursorPage, PageState, SortState } from "./types";

export const PAGE_SIZES = [10, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 250;

export interface ScopedCursorRead {
  scopeKey: string;
  variables: Record<string, unknown>;
  data?: unknown;
  error?: ErrorLike;
  loading: boolean;
  refresh: () => void;
}

export type CursorTableOptions<TRow, TVars extends OperationVariables> = {
  query: DocumentNode;
  /** Static filter variables. Changing them resets the walk to page one. */
  variables?: Partial<TVars>;
  /** Pull the page envelope out of the query response. */
  extract: (data: unknown) => CursorPage<TRow> | null | undefined;
  pageSize?: number;
  /**
   * Name of the query's server-side search argument (usually `"search"`).
   * Omit it and the search box does not render — a table whose query has
   * no search arg must not show a box that silently does nothing, which
   * is what client-side filtering over one fetched page amounted to.
   */
  searchVariable?: string;
  /**
   * Name of the query's cursor argument. The schema grew two spellings
   * before this hook existed — `after` on the events and audit pages,
   * `cursor` on the apps and activity pages — and renaming a shipped
   * argument is a breaking change for the CLI, so the hook adapts instead.
   */
  cursorVariable?: string;
  /** Name of the query's server-side sort argument, if it has one. */
  sortVariable?: string;
  initialSort?: SortState;
  /**
   * Opt into URL state under this prefix (e.g. `"dep"` → `?dep-q=…`).
   * Search and page size sync; the cursor deliberately does not. A cursor
   * is a position in a keyset walk — pasting a day-old one at someone
   * means "rows after this row", which is not the page they saw.
   */
  urlKey?: string;
  fetchPolicy?: WatchQueryFetchPolicy;
  pollInterval?: number;
  skip?: boolean;
  /** Discard a cursor chain after an explicit refresh/decision without adding API variables. */
  resetKey?: string | number;
  /** Optional actor-bound network loader; the cursor walk remains shared. */
  requestScopeKey?: string;
  externalRead?: ScopedCursorRead;
};

export type CursorTableController<TRow> = {
  rows: TRow[];
  state: PageState;
  error?: ErrorLike;
  retry: () => void;
  refetch: () => void;
  totalCount: number | null;
  /** 0-based index of the page being shown. */
  pageIndex: number;
  hasNext: boolean;
  hasPrev: boolean;
  /** Opaque server positions, exposed for the shared ListPage adapter. */
  requestVariables?: Record<string, unknown>;
  after?: string | null;
  nextCursor?: string | null;
  next: () => void;
  prev: () => void;
  pageSize: number;
  setPageSize: (n: number) => void;
  /** Live input value — updates on every keystroke. */
  search: string;
  setSearch: (s: string) => void;
  /**
   * True whenever the rows on screen are not the answer to the question
   * currently being asked — while a typed term is still debouncing, and
   * while the request it triggered is in flight. The table keeps showing
   * the previous page rather than blanking, so something has to say that
   * those rows are stale.
   */
  isStale: boolean;
  /** True while a typed term has not yet reached the server. */
  isSearching: boolean;
  searchEnabled: boolean;
  sort: SortState | undefined;
  toggleSort: (key: string) => void;
  sortEnabled: boolean;
  /** True when any filter is narrowing the result set. */
  isFiltered: boolean;
  clearFilters: () => void;
};

/** Read initial search/pageSize from the URL once, on mount. */
function readUrlState(urlKey: string | undefined) {
  if (!urlKey || typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const size = Number(params.get(`${urlKey}-size`));
  return {
    search: params.get(`${urlKey}-q`) ?? undefined,
    pageSize: PAGE_SIZES.includes(size) ? size : undefined,
  };
}

export function useCursorTable<TRow, TVars extends OperationVariables = OperationVariables>({
  query,
  variables,
  extract,
  pageSize: initialPageSize = DEFAULT_PAGE_SIZE,
  searchVariable,
  cursorVariable = "after",
  sortVariable,
  initialSort,
  urlKey,
  fetchPolicy = "cache-and-network",
  pollInterval,
  skip,
  resetKey,
  requestScopeKey,
  externalRead,
}: CursorTableOptions<TRow, TVars>): CursorTableController<TRow> {
  // Lazy initialisers: the URL is read once, on mount. Later URL changes
  // are ignored on purpose — this hook owns the query string it writes, and
  // re-reading it would fight the replaceState below.
  const [search, setSearchRaw] = React.useState(() => readUrlState(urlKey).search ?? "");
  const [pageSize, setPageSizeRaw] = React.useState(
    () => readUrlState(urlKey).pageSize ?? initialPageSize
  );
  const [sort, setSort] = React.useState<SortState | undefined>(initialSort);

  const debouncedSearch = useDebounce(search, SEARCH_DEBOUNCE_MS);
  const searchEnabled = Boolean(searchVariable);
  const sortEnabled = Boolean(sortVariable);

  // Any change to what is being asked for invalidates every cursor issued
  // against the old question: a cursor is a position in a result set, so
  // replaying one after the filter moved seeks into rows that may no longer
  // be there and the page silently skips or repeats.
  const filterKey = JSON.stringify([debouncedSearch, pageSize, sort, variables, resetKey]);

  /**
   * The walk. `stack[i]` is the cursor that produced page `i`, so page one
   * is `[null]` and `prev` is a pop rather than a second, backwards seek —
   * keyset pagination has no "before this row" without a reversed query.
   *
   * The stack is stored with the filter key it was built under and derived
   * back to `[null]` when they disagree, rather than reset from an effect.
   * An effect would render one frame with a stale cursor against fresh
   * filters, which is a real request for the wrong page.
   */
  const [walk, setWalk] = React.useState<{ key: string; stack: (string | null)[] }>({
    key: filterKey,
    stack: [null],
  });
  const stack = walk.key === filterKey ? walk.stack : [null];
  const cursor = stack[stack.length - 1];

  React.useEffect(() => {
    if (!urlKey || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const set = (key: string, value: string, isDefault: boolean) => {
      if (isDefault) params.delete(key);
      else params.set(key, value);
    };
    set(`${urlKey}-q`, debouncedSearch, debouncedSearch === "");
    set(`${urlKey}-size`, String(pageSize), pageSize === initialPageSize);
    const qs = params.toString();
    // replaceState, not router.replace: a Next navigation would remount
    // the tree and throw away the cursor stack we are trying to preserve.
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [urlKey, debouncedSearch, pageSize, initialPageSize]);

  const queryVariables = React.useMemo(() => {
    const vars: Record<string, unknown> = { ...variables, limit: pageSize };
    if (cursor) vars[cursorVariable] = cursor;
    if (searchVariable) vars[searchVariable] = debouncedSearch || null;
    if (sortVariable && sort) vars[sortVariable] = `${sort.key}:${sort.dir}`;
    return vars;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    filterKey,
    cursor,
    cursorVariable,
    searchVariable,
    sortVariable,
    debouncedSearch,
    sort,
    pageSize,
  ]);

  const network = useQuery(query, {
    variables: queryVariables,
    fetchPolicy,
    pollInterval,
    skip: skip || requestScopeKey !== undefined,
    notifyOnNetworkStatusChange: true,
  });
  const scoped = requestScopeKey !== undefined;
  const sameScope = Boolean(
    externalRead && externalRead.scopeKey === requestScopeKey && requestScopeKey
  );
  const sameRequest =
    sameScope && JSON.stringify(externalRead?.variables) === JSON.stringify(queryVariables);
  const data = scoped ? (sameRequest ? externalRead?.data : undefined) : network.data;
  const previousData = scoped ? (sameScope ? externalRead?.data : undefined) : network.previousData;
  const loading = scoped
    ? !skip && (!sameRequest || Boolean(externalRead?.loading))
    : network.loading;
  const error = scoped ? (sameRequest ? externalRead?.error : undefined) : network.error;

  // Hold the last good page while the next one is in flight, so paging
  // does not blank the table and shift the layout under the cursor.
  const page = extract(data) ?? extract(previousData);
  const rows = React.useMemo(() => page?.items ?? [], [page]);
  const totalCount = page?.totalCount ?? null;

  const isFiltered = debouncedSearch.trim().length > 0;

  const state: PageState = error
    ? "error"
    : !page && loading
      ? "loading"
      : rows.length > 0
        ? "ready"
        : isFiltered
          ? "emptyFiltered"
          : "empty";

  const setSearch = React.useCallback((value: string) => setSearchRaw(value), []);
  const setPageSize = React.useCallback((n: number) => setPageSizeRaw(n), []);

  const next = React.useCallback(() => {
    const nextCursor = page?.nextCursor;
    if (!nextCursor || loading || error || skip) return;
    setWalk((w) => ({
      key: filterKey,
      stack: [...(w.key === filterKey ? w.stack : [null]), nextCursor],
    }));
  }, [page?.nextCursor, filterKey, loading, error, skip]);

  const prev = React.useCallback(() => {
    setWalk((w) => {
      const current = w.key === filterKey ? w.stack : [null];
      return { key: filterKey, stack: current.length > 1 ? current.slice(0, -1) : current };
    });
  }, [filterKey]);

  const toggleSort = React.useCallback((key: string) => {
    setSort((current) => {
      if (current?.key !== key) return { key, dir: "asc" };
      if (current.dir === "asc") return { key, dir: "desc" };
      return undefined;
    });
  }, []);

  const clearFilters = React.useCallback(() => setSearchRaw(""), []);

  const retry = React.useCallback(() => {
    if (scoped) {
      externalRead?.refresh();
      return;
    }
    void network.refetch().catch(() => {
      // The rejection is already surfaced through `error`; swallowing it
      // here only stops an unhandled rejection in the console.
    });
  }, [scoped, externalRead, network]);

  return {
    rows,
    state,
    error,
    retry,
    refetch: retry,
    totalCount,
    pageIndex: stack.length - 1,
    hasNext: Boolean(page?.nextCursor),
    hasPrev: stack.length > 1,
    requestVariables: queryVariables,
    after: cursor,
    nextCursor: page?.nextCursor ?? null,
    next,
    prev,
    pageSize,
    setPageSize,
    search,
    setSearch,
    isStale: (search !== debouncedSearch || loading) && rows.length > 0,
    isSearching: search !== debouncedSearch,
    searchEnabled,
    sort,
    toggleSort,
    sortEnabled,
    isFiltered,
    clearFilters,
  };
}
