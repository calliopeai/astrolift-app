"use client";

import { useMemo, useState } from "react";

// ─── types ────────────────────────────────────────────────────────────────────

export type SortDirection = "asc" | "desc" | null;

export interface SortState {
  key: string | null;
  direction: SortDirection;
}

export interface ListControlsResult<T> {
  /** Filtered + sorted + paginated slice ready to render. */
  rows: T[];
  /** Total items after filtering (before pagination). */
  totalFiltered: number;
  /** Current page index (0-based). */
  pageIndex: number;
  setPageIndex: (i: number) => void;
  /** Total number of pages. */
  pageCount: number;
  /** Current page size. */
  pageSize: number;
  setPageSize: (n: number) => void;
  /** Available page size options. */
  pageSizes: number[];
  /** Current search query. */
  query: string;
  setQuery: (q: string) => void;
  /** Current sort state. */
  sort: SortState;
  /** Toggle sort for a given key. Cycles: asc → desc → off. */
  toggleSort: (key: string) => void;
}

interface ListControlsOptions<T> {
  /** The full data array (pass the query result directly). */
  data: T[] | null | undefined;
  /**
   * Return a single string used for searching.  Concatenate every field you
   * want to match against (e.g. `[item.name, item.slug].join(' ')`).
   */
  searchFn: (item: T) => string;
  /**
   * Optional sort comparator map.  Keys match the sortKey passed to
   * `<SortableHeader>` / `toggleSort`.  Return negative / zero / positive like
   * Array.prototype.sort.
   */
  sortFn?: (key: string, a: T, b: T) => number;
  initialPageSize?: number;
}

const DEFAULT_PAGE_SIZES = [10, 25, 50, 100];

// ─── hook ─────────────────────────────────────────────────────────────────────

export function useListControls<T>({
  data,
  searchFn,
  sortFn,
  initialPageSize = 25,
}: ListControlsOptions<T>): ListControlsResult<T> {
  const [query, setQueryRaw] = useState("");
  const [sort, setSort] = useState<SortState>({ key: null, direction: null });
  const [pageIndex, setPageIndexRaw] = useState(0);
  const [pageSize, setPageSizeRaw] = useState(initialPageSize);

  const setQuery = (q: string) => {
    setQueryRaw(q);
    setPageIndexRaw(0);
  };

  const setPageSize = (n: number) => {
    setPageSizeRaw(n);
    setPageIndexRaw(0);
  };

  const setPageIndex = (i: number) => {
    setPageIndexRaw(i);
  };

  const toggleSort = (key: string) => {
    setSort((prev) => {
      if (prev.key !== key) return { key, direction: "asc" };
      if (prev.direction === "asc") return { key, direction: "desc" };
      return { key: null, direction: null };
    });
    setPageIndexRaw(0);
  };

  const filtered = useMemo(() => {
    const all = data ?? [];
    if (!query.trim()) return all;
    const lower = query.toLowerCase();
    return all.filter((item) => searchFn(item).toLowerCase().includes(lower));
  }, [data, query, searchFn]);

  const sorted = useMemo(() => {
    if (!sort.key || !sortFn) return filtered;
    const key = sort.key;
    const dir = sort.direction === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => dir * sortFn(key, a, b));
  }, [filtered, sort, sortFn]);

  const totalFiltered = sorted.length;
  const pageCount = Math.max(1, Math.ceil(totalFiltered / pageSize));
  const safePageIndex = Math.min(pageIndex, pageCount - 1);
  const rows = sorted.slice(safePageIndex * pageSize, (safePageIndex + 1) * pageSize);

  return {
    rows,
    totalFiltered,
    pageIndex: safePageIndex,
    setPageIndex,
    pageCount,
    pageSize,
    setPageSize,
    pageSizes: DEFAULT_PAGE_SIZES,
    query,
    setQuery,
    sort,
    toggleSort,
  };
}
