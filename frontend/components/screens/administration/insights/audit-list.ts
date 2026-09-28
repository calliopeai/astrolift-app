/**
 * The audit trail's list declaration (spec 44 §5.1, §4.4): cursor paged,
 * views All · Mine · Denied, filters actor, action, target kind, decision
 * and since. Shared by the hook (URL state, query variables) and the
 * screen (tabs, chips), and pure, so the variable mapping is unit-tested.
 */
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";

import {
  type ListDefinition,
  type ListFieldOption,
  standardViews,
} from "@/components/list/use-list-state";

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
    // `actor` is the event's actor id; Mine sends the viewer's own id.
    { key: "actor", label: "Actor" },
    // The server matches the action exactly: `team.create`, not `team`.
    { key: "action", label: "Action" },
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
  searchPlaceholder: "Action, exact: team.create",
  defaultSort: [{ key: "occurredAt", dir: "desc" }],
  views: standardViews({ actor: "me" }, [
    { key: "denied", label: "Denied", filters: { decision: "DENY" } },
  ]),
  paging: "cursor",
  pageSizes: [25, 50, 100],
  defaultPageSize: AUDIT_PAGE_SIZE,
};

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

export interface AuditQueryVariables {
  limit: number;
  after: string | null;
  action: string | null;
  decision: string | null;
  actorId: string | null;
  createdAtGte: string | null;
  createdAtLte: null;
  includeTotal: boolean;
}

/**
 * The list state as ListAuditEventsPage's variables. The search box is the
 * exact action match until the backend has a search argument; an `action`
 * chip wins over it. `actor: me` resolves to the viewer's id, and before
 * that id is known the query waits (see `ready`), so Mine never shows
 * everyone's events for a frame.
 */
export function auditVariables(
  filters: Record<string, string>,
  q: string,
  opts: { pageSize: number; after: string | null; viewerId: string | null; now: number }
): { variables: AuditQueryVariables; ready: boolean } {
  const actor = filters.actor === "me" ? opts.viewerId : (filters.actor ?? null);
  return {
    ready: filters.actor !== "me" || Boolean(opts.viewerId),
    variables: {
      limit: opts.pageSize,
      after: opts.after,
      action: filters.action || q.trim() || null,
      decision: filters.decision || null,
      actorId: actor || null,
      createdAtGte: sinceToIso(filters.since, opts.now),
      createdAtLte: null,
      // `totalCount` is opt-in on this page: the audit list shows it.
      includeTotal: true,
    },
  };
}

/**
 * Target kind has no query argument yet, so it narrows the page in hand.
 * CLIENT-SIDE, and marked as such on the screen: a page can come back
 * shorter than its size. Case-insensitive exact match on `targetKind`.
 */
export function matchesTargetKind(row: AstroliftAuditEvent, kind: string | undefined): boolean {
  return !kind || row.targetKind.toLowerCase() === kind.toLowerCase();
}
