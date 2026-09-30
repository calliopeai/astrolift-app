/**
 * The audit trail's list declaration (spec 44 §5.1, §4.4): cursor paged,
 * views All · Mine · Denied, filters actor, action, target kind, decision
 * and since. Shared by the hook (URL state, query variables) and the
 * screen (tabs, chips), and pure, so the variable mapping is unit-tested.
 *
 * The server answers all of it (#2151): the search box is
 * `astroliftAuditEventsPage(search:)` and every chip is a field of its
 * `filter`, target kind included, so the rows, the count and the export
 * all answer the same question.
 */
import {
  type ListDefinition,
  type ListFieldOption,
  standardViews,
} from "@/components/list/list-state";

/** The `since` choices; a typed `since:2026-09-01` date is accepted too. */
export const SINCE_OPTIONS: ListFieldOption[] = [
  { value: "1h", label: "Last hour" },
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

/**
 * The audit trail keeps the 100-row page operators read it at (the old
 * hand-rolled table's default), with the smaller sizes on offer.
 */
export const AUDIT_PAGE_SIZE = 100;

export const AUDIT_LIST: ListDefinition = {
  id: "admin.audit",
  fields: [
    // `actor` is the event's actor id; Mine sends "me", which the server resolves.
    { key: "actor", label: "Actor" },
    // A chip matches the action exactly (`team.create`); the search box
    // matches a prefix (`team.`).
    { key: "action", label: "Action" },
    // Case-insensitive: `user`, `role_binding`, `app`.
    { key: "target", label: "Target kind" },
    {
      key: "decision",
      label: "Decision",
      options: [
        { value: "ALLOW", label: "ALLOW" },
        { value: "DENY", label: "DENY" },
      ],
    },
    { key: "since", label: "Since", options: SINCE_OPTIONS },
  ],
  searchPlaceholder: "Search actions, actors, targets, request ids…",
  defaultSort: [{ key: "occurredAt", dir: "desc" }],
  views: standardViews({ actor: "me" }, [
    { key: "denied", label: "Denied", filters: { decision: "DENY" } },
  ]),
  paging: "cursor",
  pageSizes: [25, 50, 100],
  defaultPageSize: AUDIT_PAGE_SIZE,
};

/** Translate presentation only; URL keys, filter values and query semantics stay stable. */
export function localizedAuditList(t: (key: string) => string): ListDefinition {
  return {
    ...AUDIT_LIST,
    searchPlaceholder: t("searchPlaceholder"),
    fields: AUDIT_LIST.fields.map((field) => ({
      ...field,
      label: t(`listFields.${field.key}`),
      options: field.options?.map((option) => ({
        ...option,
        label:
          field.key === "decision" ? t(`decisions.${option.value}`) : t(`since.${option.value}`),
      })),
    })),
    views: AUDIT_LIST.views.map((view) => ({ ...view, label: t(`views.${view.key}`) })),
  };
}

const RELATIVE = /^(\d+)([hd])$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * A `since` value as the ISO timestamp the query's `createdAtGte` takes:
 * `24h` / `7d` back from `now`, or a `yyyy-mm-dd` date at UTC midnight.
 * Anything else is null, so an unparseable chip narrows nothing rather
 * than sending the server a value it rejects.
 */
export function sinceToIso(value: string | undefined, now: number): string | null {
  if (!value) return null;
  const rel = RELATIVE.exec(value);
  if (rel) {
    const hours = Number(rel[1]) * (rel[2] === "d" ? 24 : 1);
    return new Date(now - hours * 3_600_000).toISOString();
  }
  const date = DATE.exec(value);
  if (date) {
    const ts = Date.UTC(Number(date[1]), Number(date[2]) - 1, Number(date[3]));
    return Number.isNaN(ts) ? null : new Date(ts).toISOString();
  }
  return null;
}

/** The `AstroliftAuditEventsFilter` fields the list sends; unset ones are left out. */
export interface AuditEventsFilter {
  actor?: string[];
  action?: string[];
  targetKind?: string[];
  decision?: string[];
  since?: string;
}

export interface AuditQueryVariables {
  limit: number;
  after: string | null;
  search: string | null;
  filter: AuditEventsFilter | null;
  includeTotal: boolean;
}

/**
 * The list state as ListAuditEventsPage's variables: the search box as
 * `search`, each chip as its `filter` field. `actor: me` goes as it is;
 * the server reads it as the viewer. An empty filter is `null`, so a cold
 * load asks exactly what the route preloads.
 */
export function auditVariables(
  filters: Record<string, string>,
  q: string,
  opts: { pageSize: number; after: string | null; now: number }
): AuditQueryVariables {
  const filter: AuditEventsFilter = {};
  if (filters.actor) filter.actor = [filters.actor];
  if (filters.action) filter.action = [filters.action];
  if (filters.target) filter.targetKind = [filters.target];
  if (filters.decision) filter.decision = [filters.decision];
  const since = sinceToIso(filters.since, opts.now);
  if (since) filter.since = since;
  return {
    limit: opts.pageSize,
    after: opts.after,
    search: q.trim() || null,
    filter: Object.keys(filter).length ? filter : null,
    // `totalCount` is opt-in on this page: the audit list shows it.
    includeTotal: true,
  };
}
