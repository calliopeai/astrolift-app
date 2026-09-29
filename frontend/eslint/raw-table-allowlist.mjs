/**
 * The complete list of files still importing `@/components/ui/table`
 * directly (#1243, tail of epic #1230).
 *
 * `astroliftData/no-raw-table` runs at `error`. It ran at `warn` for the
 * length of three migration waves and the count went 59 → 38 → 35 while
 * new surfaces kept shipping past it, which is the failure the rule was
 * written to prevent. A warning nobody reads is not a gate.
 *
 * Flipping to `error` needs somewhere for the residue to live, and this
 * is deliberately that place rather than 35 scattered
 * `eslint-disable-next-line` comments: one file that can be read top to
 * bottom, diffed, and counted.
 *
 * Two rules keep it from becoming a parking lot:
 *
 *   1. Every entry states a blocker, and "nobody got to it" is written
 *      down as exactly that rather than dressed up as a reason.
 *   2. `raw-table-allowlist.test.ts` asserts this list is *exactly* the
 *      set of files importing the primitives. Migrate a surface and the
 *      test fails until its entry is deleted; add a raw table and lint
 *      fails until an entry is added, in review, with a reason.
 *
 * The list may only shrink. Adding to it is a design decision, not a
 * formality.
 */

/**
 * Group 1 — the collection has no cursor-paginated field.
 *
 * `useCursorTable` is DataTable's only controller and it speaks
 * `{ items, nextCursor, totalCount }`. These surfaces read plain list
 * fields, so adopting DataTable means adding a `…Page` field to the
 * schema first. That is backend work, and it is what actually gates
 * these — not the frontend rewrite.
 */
const NO_PAGE_FIELD = [
  {
    file: "components/observability/TraceExplorerPanel.tsx",
    reason: "astroliftAppTraces returns a bounded trace window, unpaginated.",
  },
];

/**
 * Group 2 — the rows are not a server-side collection at all.
 *
 * A static constant, an array nested inside a parent object's payload, or
 * a join computed in the client. There is no query for a controller to
 * page, and `useCursorTable`'s doc block records the decision not to ship
 * a client-side counterpart: every surface picked it last time, which is
 * how `/deployments` ended up fetching `limit: 100` and paginating
 * locally. These stay on the primitives unless their data moves to the
 * server as its own collection.
 */
const NOT_A_SERVER_COLLECTION = [
  {
    file: "components/screens/apps/managed-services/EmailDetailSheet.tsx",
    reason:
      "Four tables over arrays nested in the astroliftEmailServiceDetail payload (suppressions, messages, templates) — no collection of their own.",
  },
  {
    file: "components/screens/apps/security/AppSecurityScreen.tsx",
    reason: "Findings are derived and sorted in the client from the app's event stream.",
  },
  {
    file: "components/screens/apps/workloads/WorkloadDetailScreen.tsx",
    reason: "Pod-status buckets, containers and probes all render off the single workload payload.",
  },
  {
    file: "components/screens/clusters/settings/ClusterSettings.tsx",
    reason:
      "One table iterates the static CAPABILITY_KEYS constant; another iterates installedReleases nested in a bootstrap run.",
  },
  {
    file: "components/screens/documentation/ConfigurationScreen.tsx",
    reason: "A static environment-variable reference. The page issues no query.",
  },
  {
    file: "components/screens/documentation/DriversScreen.tsx",
    reason: "A capability matrix computed by crossing provider plugins with registered clusters.",
  },
];

/**
 * Group 3 — a row shape DataTable does not model.
 */
const UNSUPPORTED_ROW_SHAPE = [];

/**
 * Group 4 — no blocker. These were owed a migration, and are done.
 *
 * Empty, and worth keeping rather than deleting: it is where a surface
 * goes when someone adds a raw table with no reason not to use DataTable,
 * and an empty array makes that entry obvious in review. Naming the group
 * "nobody got to it" rather than inventing a blocker is what let it be
 * emptied — every other group here is still populated because its entries
 * have real ones.
 *
 * Two of the eight turned out to have blockers after all once read:
 * cronjob-home moved to group 1 (its field filters on the app, not the
 * workload — #1512), and roles-tab's stated reason was wrong. That is the
 * argument for the reasons being specific enough to check.
 */
const PENDING_MIGRATION = [];

export const RAW_TABLE_ALLOWLIST = [
  ...NO_PAGE_FIELD,
  ...NOT_A_SERVER_COLLECTION,
  ...UNSUPPORTED_ROW_SHAPE,
  ...PENDING_MIGRATION,
];

export const RAW_TABLE_ALLOWLIST_FILES = RAW_TABLE_ALLOWLIST.map((entry) => entry.file);
