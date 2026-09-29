/**
 * Filter, search, sort and page a whole set in the browser, for the app
 * lists whose backend field returns every row at once with no filter, sort
 * or page (Leo's list rule 4: "unpaginated backend fields page, sort and
 * filter client-side for now with a ListView note"). Each list declares how
 * a row matches a filter, which text search reads and how a sort key
 * compares; this does the rest, stably. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { ListState } from "@/components/list/use-list-state";

/** The note each such list's view carries while the field is unpaginated. */
export const CLIENT_LIST_NOTE =
  "Filtered, sorted and paged in the browser: this list's backend field returns every row at once.";

export interface ClientListSpec<T> {
  /** True when the row passes one filter (`key` is the field, `value` the chip). */
  matches: (row: T, key: string, value: string) => boolean;
  /** The strings search looks in. */
  text: (row: T) => (string | null | undefined)[];
  /** Compares two rows on one sort key, ascending. */
  compare: (a: T, b: T, key: string) => number;
  /** Breaks ties so the order is stable between renders. */
  tie: (a: T, b: T) => number;
}

export function selectClientRows<T>(
  all: T[],
  filters: Record<string, string>,
  state: Pick<ListState, "q" | "sort" | "page" | "pageSize">,
  spec: ClientListSpec<T>
): { rows: T[]; totalCount: number } {
  const needle = state.q.trim().toLowerCase();
  const matched = all.filter(
    (row) =>
      Object.entries(filters).every(([key, value]) => spec.matches(row, key, value)) &&
      (!needle || spec.text(row).some((f) => (f ?? "").toLowerCase().includes(needle)))
  );
  const sorted = sortRows(matched, state.sort, spec);
  const start = (Math.max(1, state.page) - 1) * state.pageSize;
  return { rows: sorted.slice(start, start + state.pageSize), totalCount: matched.length };
}

function sortRows<T>(rows: T[], sort: SortState[], spec: ClientListSpec<T>): T[] {
  return [...rows].sort((a, b) => {
    for (const s of sort) {
      const c = spec.compare(a, b, s.key);
      if (c !== 0) return s.dir === "asc" ? c : -c;
    }
    return spec.tie(a, b);
  });
}
