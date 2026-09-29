/**
 * Client-side filter, search, sort and numbered paging for a list whose
 * backend field returns every row at once (Leo's list rule 4, 2026-09-28).
 * The list page declares how a row matches a filter and what each sort key
 * reads; this runs the rest, so each surface does not write its own copy.
 * When the field grows `filter`, `search`, `sort` and `page` arguments the
 * hook sends them instead, and the screen does not change.
 */
import type { SortState } from "@/components/data-table";

export interface SelectRowsSpec<T> {
  /** One filter value against a row. Unknown keys keep the row. */
  filter?: Record<string, (row: T, value: string) => boolean>;
  /** The text a search matches, lower-cased by the caller or not. */
  text: (row: T) => (string | null | undefined)[];
  /** What each sort key compares. */
  sort?: Record<string, (row: T) => string | number>;
  /** The tie-breaker, so the order is stable across renders. */
  id: (row: T) => string;
}

export interface SelectRowsState {
  filters: Record<string, string>;
  q: string;
  sort: SortState[];
  page: number;
  pageSize: number;
}

/** The rows that match, in order: every page, before paging. */
export function matchRows<T>(
  all: readonly T[],
  { filters, q, sort }: Pick<SelectRowsState, "filters" | "q" | "sort">,
  spec: SelectRowsSpec<T>
): T[] {
  const needle = q.trim().toLowerCase();
  return all
    .filter((row) => {
      for (const [key, value] of Object.entries(filters)) {
        const test = spec.filter?.[key];
        if (value && test && !test(row, value)) return false;
      }
      if (!needle) return true;
      return spec.text(row).some((f) => (f ?? "").toLowerCase().includes(needle));
    })
    .sort((a, b) => {
      for (const s of sort) {
        const value = spec.sort?.[s.key];
        if (!value) continue;
        const x = value(a);
        const y = value(b);
        if (x < y) return s.dir === "asc" ? -1 : 1;
        if (x > y) return s.dir === "asc" ? 1 : -1;
      }
      const x = spec.id(a);
      const y = spec.id(b);
      return x < y ? -1 : x > y ? 1 : 0;
    });
}

/** One numbered page of the matching rows; `totalCount` is the filtered count. */
export function selectRows<T>(
  all: readonly T[],
  state: SelectRowsState,
  spec: SelectRowsSpec<T>
): { rows: T[]; totalCount: number } {
  const kept = matchRows(all, state, spec);
  const start = (Math.max(1, state.page) - 1) * state.pageSize;
  return { rows: kept.slice(start, start + state.pageSize), totalCount: kept.length };
}
