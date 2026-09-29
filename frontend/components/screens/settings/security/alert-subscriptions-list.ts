/**
 * Settings › Notifications › App alert subscriptions (spec 44 §5.1): the
 * embedded list's declaration and the query variables it sends.
 * `astroliftAppsPage` takes `search`, `teamSlug`, `projectSlug`, `limit`
 * and `cursor`, so those go to the server and the order is the server's
 * (newest first). Whether the viewer hears about an app comes from their
 * own subscriptions, not the apps query, so Mine (apps you get at least one
 * alert for) and Alerts narrow a wider page (`NARROW_LIMIT`) in the client.
 */
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftUserAlertSubscription } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { ALERT_KINDS, subKey } from "./alert-kinds";

export const NARROW_LIMIT = 100;

export const ALERT_SUBSCRIPTIONS_LIST: ListDefinition = {
  id: "settings.alert-subscriptions",
  fields: [
    { key: "team", label: "Team" },
    { key: "project", label: "Project" },
    {
      key: "alerts",
      label: "Alerts",
      options: [
        { value: "on", label: "subscribed" },
        { value: "off", label: "not subscribed" },
      ],
    },
  ],
  // The server matches app name and slug.
  searchPlaceholder: "Search apps, slugs…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ alerts: "on" }, [], {
    mineNote: `Mine keeps the apps you get at least one alert for among the newest ${NARROW_LIMIT}.`,
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function alertSubscriptionsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    search: q.trim() || null,
    teamSlug: filters.team || null,
    projectSlug: filters.project || null,
    limit: filters.alerts ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    cursor: after,
  };
}

/** Subscribed or not, over the rows in hand. */
export function narrowAlertApps(
  rows: AstroliftRegisteredApp[],
  filters: Record<string, string>,
  subMap: Map<string, AstroliftUserAlertSubscription>
): AstroliftRegisteredApp[] {
  if (!filters.alerts) return rows;
  return rows.filter((a) => {
    const on = ALERT_KINDS.some((k) => subMap.get(subKey(a.slug, k.value))?.enabled === true);
    return filters.alerts === "on" ? on : !on;
  });
}
