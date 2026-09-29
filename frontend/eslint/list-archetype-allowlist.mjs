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
 * The list may only shrink.
 */

/**
 * Group 1: a DataTable on `useCursorTable`. ListPage takes a `useListState`
 * controller and the spec 44 §5.1 variables (`filter`, `search`, `sort`,
 * `first` / `after`), so the hook moves first, then the screen.
 */
const ON_CURSOR_TABLE = [
  {
    file: "components/screens/agents/list/BoxesTabView.tsx",
    reason:
      "Walks agent boxes with useCursorTable in use-agent-boxes.ts; the hook moves to useListState first.",
  },
  {
    file: "components/screens/workflows/list/WorkflowsListScreen.tsx",
    reason:
      "Five views on useCursorTable in use-workflows-list.ts; they become standardViews on useListState.",
  },
];

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
const CLIENT_ROWS = [
  {
    file: "components/viz/fleet/FleetList.tsx",
    reason:
      "FleetView's 'list' style over one snapshot's agents; whether a viz style is a ListPage is not decided.",
  },
  {
    file: "components/viz/workflow/WorkflowList.tsx",
    reason:
      "WorkflowView's 'list' style over one snapshot's stages and runs; whether a viz style is a ListPage is not decided.",
  },
];

export const LIST_ARCHETYPE_ALLOWLIST = [
  ...ON_CURSOR_TABLE,
  ...TWO_LISTS,
  ...FEED_OR_SUMMARY,
  ...CLIENT_ROWS,
];

export const LIST_ARCHETYPE_ALLOWLIST_FILES = LIST_ARCHETYPE_ALLOWLIST.map((entry) => entry.file);
