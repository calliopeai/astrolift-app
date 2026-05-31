import * as React from "react";

export type SortDirection = "asc" | "desc";

export interface SortState {
  key: string;
  dir: SortDirection;
}

export interface ListControlsOptions<T> {
  /** Full dataset to control. */
  data: T[];
  /** Called on every item to produce the searchable text blob. */
  searchFn?: (item: T) => string;
  /** Initial page size (default 25). */
  initialPageSize?: number;
  /** Supported page sizes shown in the selector. */
  pageSizes?: number[];
  /** Initial sort state. */
  initialSort?: SortState;
  /** Comparator — required when you want column sorting. */
  sortFn?: (a: T, b: T, sort: SortState) => number;
}

export interface ListControlsResult<T> {
  /** Filtered, sorted, paginated rows for this page. */
  rows: T[];
  /** Total rows after filtering (before pagination). */
  totalFiltered: number;
  /** Total pages. */
  pageCount: number;
  /** Current 0-based page index. */
  pageIndex: number;
  setPageIndex: (i: number) => void;
  /** Current page size. */
  pageSize: number;
  setPageSize: (n: number) => void;
  /** Supported page sizes. */
  pageSizes: number[];
  /** Current search query. */
  query: string;
  setQuery: (q: string) => void;
  /** Current sort state (undefined = no sort). */
  sort: SortState | undefined;
  /** Toggle sort by key. Cycles: none → asc → desc → none. */
  toggleSort: (key: string) => void;
}

const DEFAULT_PAGE_SIZES = [10, 25, 50, 100];

export function useListControls<T>({
  data,
  searchFn,
  initialPageSize = 25,
  pageSizes = DEFAULT_PAGE_SIZES,
  initialSort,
  sortFn,
}: ListControlsOptions<T>): ListControlsResult<T> {
  const [query, setQueryRaw] = React.useState("");
  const [pageIndex, setPageIndexRaw] = React.useState(0);
  const [pageSize, setPageSizeRaw] = React.useState(initialPageSize);
  const [sort, setSort] = React.useState<SortState | undefined>(initialSort);

  // Reset to page 0 whenever query or page size changes.
  const setQuery = React.useCallback((q: string) => {
    setQueryRaw(q);
    setPageIndexRaw(0);
  }, []);
  const setPageSize = React.useCallback((n: number) => {
    setPageSizeRaw(n);
    setPageIndexRaw(0);
  }, []);
  const setPageIndex = React.useCallback((i: number) => setPageIndexRaw(i), []);

  const toggleSort = React.useCallback((key: string) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return undefined; // cycle back to no sort
    });
    setPageIndexRaw(0);
  }, []);

  // 1. Filter
  const filtered = React.useMemo(() => {
    if (!query.trim() || !searchFn) return data;
    const q = query.toLowerCase();
    return data.filter((item) => searchFn(item).toLowerCase().includes(q));
  }, [data, query, searchFn]);

  // 2. Sort
  const sorted = React.useMemo(() => {
    if (!sort || !sortFn) return filtered;
    return [...filtered].sort((a, b) => sortFn(a, b, sort));
  }, [filtered, sort, sortFn]);

  // 3. Paginate
  const totalFiltered = sorted.length;
  const pageCount = Math.max(1, Math.ceil(totalFiltered / pageSize));
  const safePage = Math.min(pageIndex, pageCount - 1);
  const rows = sorted.slice(safePage * pageSize, (safePage + 1) * pageSize);

  return {
    rows,
    totalFiltered,
    pageCount,
    pageIndex: safePage,
    setPageIndex,
    pageSize,
    setPageSize,
    pageSizes,
    query,
    setQuery,
    sort,
    toggleSort,
  };
}
