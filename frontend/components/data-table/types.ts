import type * as React from "react";

/**
 * The page envelope every Astrolift list query speaks.
 *
 * `nextCursor` and `totalCount` are nullable AND optional because that is
 * exactly how codegen emits them (`graphql/__generated__/operations.ts`);
 * accepting anything narrower would force a cast at every call site.
 */
export type CursorPage<TRow> = {
  items: TRow[];
  nextCursor?: string | null;
  totalCount?: number | null;
};

/**
 * What the table is showing right now. A union rather than a bag of
 * booleans so the render is a total switch — the "renders nothing"
 * variants the audit found were all a missing `else`.
 */
export type PageState =
  | "loading"
  | "error"
  /** No rows, and no filter is narrowing them: this list is genuinely empty. */
  | "empty"
  /** No rows because the search/filters excluded them all. */
  | "emptyFiltered"
  | "ready";

export type SortDirection = "asc" | "desc";

export type SortState = { key: string; dir: SortDirection };

export type Column<TRow> = {
  /** Stable identity; also the React key. */
  id: string;
  header: React.ReactNode;
  cell: (row: TRow) => React.ReactNode;
  /**
   * Tailwind width class (e.g. `"w-32"`). Drives both the real column and
   * the width of its loading skeleton, so the skeleton cannot drift from
   * the table it is standing in for.
   */
  width?: string;
  align?: "left" | "right" | "center";
  headClassName?: string;
  cellClassName?: string;
  /**
   * Server-side sort key. Present = the header renders as a sort control.
   * Absent = a plain header. There is no client-side sort: reordering the
   * single page in hand while the rest of the result set sits on the
   * server produces an order that is wrong at every page boundary.
   */
  sortKey?: string;
};

/** Copy for the two distinct empty states. Both are required by DataTable. */
export type EmptyStateSpec = {
  icon: React.ReactNode;
  title: string;
  description?: string;
  actionHref?: string;
  actionLabel?: string;
  learnMoreHref?: string;
  learnMoreLabel?: string;
};
