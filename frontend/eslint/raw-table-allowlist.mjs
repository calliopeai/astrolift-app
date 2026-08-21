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
    file: "app/(app)/administration/organization/trusted-domains-card.tsx",
    reason: "astroliftOrganizationAllowlistDomains is an unpaginated list field.",
  },
  {
    file: "app/(app)/agents/[agentSlug]/components/run-content.tsx",
    reason: "agentTasks is an unpaginated list field.",
  },
  {
    file: "app/(app)/agents/agents-client.tsx",
    reason:
      "Two of its three tables read agentTasks, unpaginated; the third is still on the legacy useListControls.",
  },
  {
    file: "app/(app)/apps/[slug]/domains/domains-client.tsx",
    reason: "All three tables read astroliftAppDomains, an unpaginated list field.",
  },
  {
    file: "app/(app)/apps/[slug]/observability/observability-client.tsx",
    reason:
      "The pod table reads astroliftAppPods, unpaginated. The alert-rule and alert-event tables beside it are Page-capable and could move independently.",
  },
  {
    file: "app/(app)/apps/[slug]/secrets/secrets-client.tsx",
    reason:
      "astroliftAppSecrets and astroliftAppSecretBundleAttachments are unpaginated list fields.",
  },
  {
    file: "app/(app)/cost/cost-client.tsx",
    reason:
      "astroliftBudgets is unpaginated; the second table is a client-side rollup of the cost-by-binding response.",
  },
  {
    file: "app/(app)/domains/domains-client.tsx",
    reason: "astroliftManagedDomains is an unpaginated list field.",
  },
  {
    file: "app/(app)/environments/environments-client.tsx",
    reason: "astroliftEnvironments is an unpaginated list field.",
  },
  {
    file: "app/(app)/metrics/metrics-client.tsx",
    reason: "astroliftAppHealthSummary is an unpaginated list field.",
  },
  {
    file: "app/(app)/pipelines/[id]/secrets-tab.tsx",
    reason: "astroliftPipelineSecrets is an unpaginated list field.",
  },
  {
    file: "app/(app)/quotas/quotas-client.tsx",
    reason: "astroliftQuotas is an unpaginated list field.",
  },
  {
    file: "app/(app)/settings/identity-provider/identity-provider-client.tsx",
    reason: "astroliftIdentityProviders is an unpaginated list field.",
  },
  {
    file: "app/(app)/settings/security/security-settings-client.tsx",
    reason: "astroliftActiveSessions is an unpaginated list field.",
  },
  {
    file: "app/(app)/teams/[slug]/team-members-panel.tsx",
    reason: "astroliftTeamMembers is an unpaginated list field.",
  },
  {
    file: "components/observability/DnsRecordsCard.tsx",
    reason: "astroliftAppDnsRecords is an unpaginated list field.",
  },
  {
    file: "components/observability/EndpointMetricsPanel.tsx",
    reason: "astroliftAppEndpointMetrics returns a fixed per-route metric set, unpaginated.",
  },
  {
    file: "components/observability/TlsCertificatesCard.tsx",
    reason: "astroliftAppCertificates is an unpaginated list field.",
  },
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
    file: "app/(app)/apps/[slug]/managed-services/email-detail-sheet.tsx",
    reason:
      "Four tables over arrays nested in the astroliftEmailServiceDetail payload (suppressions, messages, templates) — no collection of their own.",
  },
  {
    file: "app/(app)/apps/[slug]/security/security-client.tsx",
    reason: "Findings are derived and sorted in the client from the app's event stream.",
  },
  {
    file: "app/(app)/apps/[slug]/workloads/[workloadSlug]/workload-detail-client.tsx",
    reason: "Pod-status buckets, containers and probes all render off the single workload payload.",
  },
  {
    file: "app/(app)/clusters/[slug]/settings/cluster-settings-client.tsx",
    reason:
      "One table iterates the static CAPABILITY_KEYS constant; another iterates installedReleases nested in a bootstrap run.",
  },
  {
    file: "app/(app)/documentation/configuration/page.tsx",
    reason: "A static environment-variable reference. The page issues no query.",
  },
  {
    file: "app/(app)/documentation/drivers/drivers-client.tsx",
    reason: "A capability matrix computed by crossing provider plugins with registered clusters.",
  },
  {
    file: "app/(app)/projects/[slug]/project-detail-client.tsx",
    reason:
      "The agents table is a client-side join across three queries; only the apps table beside it is a server collection.",
  },
];

/**
 * Group 3 — a row shape DataTable does not model.
 */
const UNSUPPORTED_ROW_SHAPE = [
  {
    file: "app/(app)/apps/[slug]/deployments/deployments-client.tsx",
    reason:
      "Rows expand into a full-width detail panel row. DataTable's column model has no row-expansion slot, so migrating means designing one first.",
  },
];

/**
 * Group 4 — no blocker. These are owed a migration.
 *
 * The field is Page-capable and the row shape is ordinary; nothing stands
 * in the way except that the work has not been done. Recorded honestly
 * instead of given a reason it does not have, because the only thing that
 * makes this list shrink is being able to see which entries have no
 * excuse.
 */
const PENDING_MIGRATION = [
  {
    file: "app/(app)/apps/[slug]/managed-services/managed-services-client.tsx",
    reason: "No blocker: astroliftManagedServicesPage exists.",
  },
  {
    file: "app/(app)/workflows/page.tsx",
    reason:
      "No blocker: the configured-workflows table reads `workflows`, and workflowsPage exists.",
  },
];

export const RAW_TABLE_ALLOWLIST = [
  ...NO_PAGE_FIELD,
  ...NOT_A_SERVER_COLLECTION,
  ...UNSUPPORTED_ROW_SHAPE,
  ...PENDING_MIGRATION,
];

export const RAW_TABLE_ALLOWLIST_FILES = RAW_TABLE_ALLOWLIST.map((entry) => entry.file);
