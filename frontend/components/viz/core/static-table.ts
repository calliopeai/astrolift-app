import type { CursorTableController } from "@/components/data-table";

const noop = () => {};

/**
 * A DataTable controller over rows already in hand (a live viz snapshot):
 * one page, no search, no sort, no fetch. The list viz styles read the same
 * snapshot the pictures do, so there is nothing to page through.
 */
export function staticController<TRow>(rows: TRow[]): CursorTableController<TRow> {
  return {
    rows,
    state: rows.length === 0 ? "empty" : "ready",
    error: undefined,
    retry: noop,
    refetch: noop,
    totalCount: null,
    pageIndex: 0,
    hasNext: false,
    hasPrev: false,
    next: noop,
    prev: noop,
    pageSize: Math.max(rows.length, 1),
    setPageSize: noop,
    search: "",
    setSearch: noop,
    isStale: false,
    isSearching: false,
    searchEnabled: false,
    sort: undefined,
    toggleSort: noop,
    sortEnabled: false,
    isFiltered: false,
    clearFilters: noop,
  };
}
