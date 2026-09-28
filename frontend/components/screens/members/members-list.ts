import type { ListDefinition } from "@/components/list/use-list-state";

export type MembersView = "all" | "invited" | "invitations" | "bindings";

/**
 * Admin › Members (spec 44 §5.1). The page held three stacked tables (people,
 * role bindings, invitations); they are views of the one list now, so the
 * header has a single row of tabs.
 *
 * Every query behind it is `search` + `limit` + `after` only. There is no
 * role or team filter, no "shares a team with me" (Mine), no admins-only
 * read and no page numbers, so the list declares no filter fields, cursor
 * paging, and only the views the server can answer. Pending and resolved
 * invitations were one table behind a "Show resolved" toggle; they are
 * the Invited and Invitation history views (the query's `status` argument).
 */
export const MEMBERS_LIST: ListDefinition = {
  id: "admin.members",
  fields: [],
  searchPlaceholder: "Search by username or email…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [
    { key: "all", label: "All", filters: {} },
    { key: "invited", label: "Invited", filters: { status: "pending" } },
    { key: "invitations", label: "Invitation history", filters: {} },
    { key: "bindings", label: "Role bindings", filters: {} },
  ],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** Each view searches different fields, so it says so in the box. */
export const MEMBERS_SEARCH_PLACEHOLDER: Record<MembersView, string> = {
  all: "Search by username or email…",
  invited: "Search by email, role, or inviter…",
  invitations: "Search by email, role, or inviter…",
  bindings: "Search by user, group, or role…",
};
