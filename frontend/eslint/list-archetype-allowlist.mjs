/**
 * The files still rendering `DataTable` directly (Leo's list rules 3 to 5,
 * 2026-09-28).
 *
 * `astroliftData/list-archetype` runs at `error`: a list is `ListPage`
 * (full, or embedded in a detail tab), a second list on an overview is a
 * `ListSummary`, and a thing that grows is a `Feed`. `DataTable` is the
 * table under `ListPage`, not a surface's own choice. This file is where the
 * surfaces that predate the rule wait, one entry per file with its blocker,
 * matched by exact path (as `raw-table-allowlist.mjs` is, for the same
 * `[slug]` reason). `list-archetype-allowlist.test.ts` holds it exactly in
 * step with the tree: migrate a surface and the test fails until its entry
 * is deleted.
 *
 * The baseline is empty (#2126, spec 44 section 9): every group below is
 * an empty array, and the exact-match test keeps it that way. Adding an
 * entry is a design decision, made in review with its blocker written
 * down, not a way to land a surface that skips the list primitives.
 */

/**
 * Group 1: a DataTable on `useCursorTable`. ListPage takes a `useListState`
 * controller and the spec 44 §5.1 variables (`filter`, `search`, `sort`,
 * `first` / `after`), so the hook moves first, then the screen.
 */
const ON_CURSOR_TABLE = [];

/**
 * Group 2: two tables on one screen (rule 3). One stays the screen's list;
 * the other moves to its own tab or section, or becomes a ListSummary or a
 * Feed. Each also needs its hook moved off `useCursorTable` as in group 1.
 */
const TWO_LISTS = [];

/**
 * Group 3: a stream that grows (rule 5), which goes to `Feed`, or a list
 * inside an Overview (rule 3), which goes to `ListSummary`. Not ListPage.
 */
const FEED_OR_SUMMARY = [];

/**
 * Group 4: rows that are not a paged collection. They go to ListPage with
 * client-side paging and a ListView note until the field pages
 * (needsBackend), or, for the viz list styles, stay a decision to make.
 */
const CLIENT_ROWS = [];

export const LIST_ARCHETYPE_ALLOWLIST = [
  ...ON_CURSOR_TABLE,
  ...TWO_LISTS,
  ...FEED_OR_SUMMARY,
  ...CLIENT_ROWS,
];

export const LIST_ARCHETYPE_ALLOWLIST_FILES = LIST_ARCHETYPE_ALLOWLIST.map((entry) => entry.file);
