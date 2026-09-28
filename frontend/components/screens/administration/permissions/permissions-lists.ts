import type { ListDefinition } from "@/components/list/use-list-state";

/**
 * Admin › Permissions › Roles (spec 44 §5.1). `astroliftRolesPage` takes
 * `search`, `limit` and `after`: it cannot filter System from Custom or answer
 * "Mine", and has no page numbers, so the list declares one view, no filter
 * fields and cursor paging until the query grows them.
 */
export const ROLES_LIST: ListDefinition = {
  id: "admin.permissions.roles",
  fields: [],
  searchPlaceholder: "Search roles by name or slug…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/**
 * Admin › Permissions › Assignments: every role binding. The page query
 * searches user, group and role; it has no role, scope or "Mine" filter and
 * no page numbers.
 */
export const ASSIGNMENTS_LIST: ListDefinition = {
  id: "admin.permissions.assignments",
  fields: [],
  searchPlaceholder: "Search by user, group, or role…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};
