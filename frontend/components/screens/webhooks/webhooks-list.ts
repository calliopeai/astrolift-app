/**
 * Admin › Webhooks (spec 44 §4.4, §5.1): the list declaration and the query
 * variables it sends. `astroliftWebhookSubscriptionsPage` takes `appSlug`,
 * `search`, `limit` and `after`: search and the cursor go to the server,
 * and the order is the server's. Status, format and Failing have no
 * argument yet, so they narrow a wider page (`NARROW_LIMIT`) in the client
 * and the views that lean on it say so. Subscriptions record no creator, so
 * Mine is empty until they do.
 */
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";

export const NARROW_LIMIT = 100;

const NARROWED = `Keeps the matching subscriptions among the newest ${NARROW_LIMIT}, until the webhooks query takes filters.`;

export const WEBHOOKS_LIST: ListDefinition = {
  id: "admin.webhooks",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "active" },
        { value: "paused", label: "paused" },
      ],
    },
    {
      key: "format",
      label: "Format",
      options: [
        { value: "generic", label: "generic" },
        { value: "slack", label: "slack" },
        { value: "discord", label: "discord" },
      ],
    },
    {
      key: "failing",
      label: "Failing",
      options: [{ value: "yes", label: "has failures" }],
    },
  ],
  // The server matches the delivery URL, not the event list.
  searchPlaceholder: "Search delivery URLs…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews(
    { createdBy: "me" },
    [
      { key: "failing", label: "Failing", filters: { failing: "yes" }, note: NARROWED },
      { key: "paused", label: "Paused", filters: { status: "paused" }, note: NARROWED },
    ],
    { mineNote: "Webhook subscriptions do not record who created them yet, so Mine is empty." }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function narrows(filters: Record<string, string>): boolean {
  return Boolean(filters.status || filters.format || filters.failing || filters.createdBy);
}

export function webhooksVariables(
  appSlug: string | null,
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    appSlug,
    search: q.trim() || null,
    limit: narrows(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowWebhooks(
  rows: AstroliftWebhookSubscription[],
  filters: Record<string, string>
): AstroliftWebhookSubscription[] {
  // No creator on the row: Mine holds nothing, never everything.
  if (filters.createdBy) return [];
  return rows.filter((s) => {
    if (filters.status && (s.isActive ? "active" : "paused") !== filters.status) return false;
    if (filters.format && s.format !== filters.format) return false;
    if (filters.failing && s.failureCount === 0) return false;
    return true;
  });
}
