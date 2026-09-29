/**
 * Admin › Alerts (spec 44 §4.4, §5.1): the rules list declaration and the
 * query variables it sends, and the events feed's views.
 *
 * Rules: `astroliftAlertRulesPage` takes `target`, `targetId`, `activeOnly`,
 * `search`, `limit` and `after`, so target, the Active view and search go to
 * the server and the order is the server's. Severity and Muted have no
 * argument yet: they narrow a wider page (`NARROW_LIMIT`) in the client, and
 * the views that lean on it say so. Rules record no creator, so Mine is empty.
 *
 * Events (their own route, /alerts/events): a Feed on
 * `astroliftAlertEventsPage`, All or Firing (`unresolvedOnly`).
 */
import { type ListDefinition, standardViews } from "@/components/list/list-state";

import type { AlertRule } from "./use-alerts";

export const NARROW_LIMIT = 100;

const TARGETS = ["global", "app", "environment", "workload", "managed_service"];
const SEVERITIES = ["info", "warn", "critical"];

export const ALERT_RULES_LIST: ListDefinition = {
  id: "admin.alerts.rules",
  fields: [
    { key: "target", label: "Target", options: TARGETS.map((t) => ({ value: t, label: t })) },
    { key: "targetId", label: "Target ID" },
    {
      key: "severity",
      label: "Severity",
      options: SEVERITIES.map((s) => ({ value: s, label: s })),
    },
    {
      key: "state",
      label: "State",
      options: [
        { value: "active", label: "active" },
        { value: "muted", label: "muted" },
        { value: "inactive", label: "inactive" },
      ],
    },
  ],
  // The server matches the rule name and the target it covers.
  searchPlaceholder: "Search rules, targets…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews(
    { createdBy: "me" },
    [
      { key: "active", label: "Active", filters: { state: "active" } },
      {
        key: "muted",
        label: "Muted",
        filters: { state: "muted" },
        note: `Keeps the muted rules among the newest ${NARROW_LIMIT}, until the rules query takes a mute filter.`,
      },
    ],
    { mineNote: "Alert rules do not record who created them yet, so Mine is empty." }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** True when a filter the server cannot answer is on. */
export function narrows(filters: Record<string, string>): boolean {
  return Boolean(
    filters.severity || filters.createdBy || (filters.state && filters.state !== "active")
  );
}

export function rulesVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    target: filters.target || null,
    targetId: filters.targetId || null,
    activeOnly: filters.state === "active",
    search: q.trim() || null,
    limit: narrows(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowRules(rows: AlertRule[], filters: Record<string, string>): AlertRule[] {
  if (filters.createdBy) return [];
  return rows.filter((r) => {
    if (filters.severity && r.severity !== filters.severity) return false;
    if (filters.state === "muted" && !r.activeMute) return false;
    if (filters.state === "inactive" && r.isActive) return false;
    return true;
  });
}

export type AlertEventsView = "all" | "firing";

export function alertEventsView(raw: string | null | undefined): AlertEventsView {
  return raw === "firing" ? "firing" : "all";
}
