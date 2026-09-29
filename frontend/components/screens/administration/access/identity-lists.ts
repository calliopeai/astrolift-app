/**
 * The identity lists on the list contract (#2153): members, invitations,
 * teams, roles, role bindings and policies take `filter`, `sort`, `page`
 * and `pageSize`, and any of the last three selects numbered paging with an
 * exact `totalCount`. Pure and server-safe: the list state as those
 * variables, so a route's preload and the hook ask the same question.
 */
import type { SortState } from "@/components/data-table";
import { formatSort } from "@/components/list/list-state";

export interface NumberedVariables<F> {
  search: string | null;
  filter: F | null;
  sort: string;
  page: number;
  pageSize: number;
}

/**
 * `sort` is always sent, so the server pages by number; an empty filter is
 * `null`, so a cold load has one spelling.
 */
export function numberedVariables<F extends object>(
  state: { q: string; sort: SortState[]; page: number; pageSize: number },
  filter: F
): NumberedVariables<F> {
  const clean = Object.fromEntries(
    Object.entries(filter).filter(([, v]) => v !== undefined && v !== null)
  ) as F;
  return {
    search: state.q.trim() || null,
    filter: Object.keys(clean).length ? clean : null,
    sort: formatSort(state.sort),
    page: Math.max(1, state.page),
    pageSize: state.pageSize,
  };
}

/** A chip's value as the one-value list a filter field takes. */
export function one(value: string | undefined): string[] | undefined {
  return value ? [value] : undefined;
}

/** `yes` / `no` (or `true` / `false`) as a Boolean filter field. */
export function yesNo(value: string | undefined): boolean | undefined {
  if (value === "yes" || value === "true") return true;
  if (value === "no" || value === "false") return false;
  return undefined;
}
