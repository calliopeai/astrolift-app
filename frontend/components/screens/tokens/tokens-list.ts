/**
 * Admin › API keys (spec 44 §4.4, §5.1): the list declaration and the query
 * variables it sends. `astroliftApiTokensPage` takes `search`, `limit` and
 * `after`, so search and the cursor go to the server and the order is the
 * server's (newest first). Status, scope and Mine have no argument yet: they
 * narrow a wider page (`NARROW_LIMIT`) in the client, and the views that
 * lean on it say so. When the query grows `filter` and `sort` the hook sends
 * them and this step goes away; the screen does not change.
 */
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftApiToken } from "@/graphql/identity/identity.types";

/** The page walked when a client-side filter narrows it. */
export const NARROW_LIMIT = 100;

const NARROWED = `Keeps the matching keys among the newest ${NARROW_LIMIT}, until the API keys query takes filters.`;

export const TOKENS_LIST: ListDefinition = {
  id: "admin.tokens",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "active" },
        { value: "revoked", label: "revoked" },
      ],
    },
    { key: "scope", label: "Scope" },
  ],
  // The server matches name, owner, team and the last-4 suffix.
  searchPlaceholder: "Search keys, owners, teams, suffixes…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews(
    { owner: "me" },
    [
      { key: "active", label: "Active", filters: { status: "active" }, note: NARROWED },
      { key: "revoked", label: "Revoked", filters: { status: "revoked" }, note: NARROWED },
    ],
    { mineNote: `Mine keeps the keys you own among the newest ${NARROW_LIMIT}.` }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** True when a filter the server cannot answer is on. */
export function narrows(filters: Record<string, string>): boolean {
  return Boolean(filters.status || filters.scope || filters.owner);
}

export function tokensVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    search: q.trim() || null,
    limit: narrows(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

/** Status, scope and Mine over the rows in hand. `me` is the viewer's username. */
export function narrowTokens(
  rows: AstroliftApiToken[],
  filters: Record<string, string>,
  me: string | null
): AstroliftApiToken[] {
  return rows.filter((t) => {
    if (filters.status && (t.isRevoked ? "revoked" : "active") !== filters.status) return false;
    if (filters.scope && !t.scopes.some((s) => s.includes(filters.scope!))) return false;
    if (filters.owner === "me" && (!me || t.user?.username !== me)) return false;
    return true;
  });
}
