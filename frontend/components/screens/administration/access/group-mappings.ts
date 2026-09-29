/**
 * An IdP group's role mappings list (#2157), under the group's grants on its
 * Access tab. Numbered pages of `astroliftGroupRoleMappingsPage`, held to the
 * group; the list state lives in memory because the grants list above it
 * owns the page's query string. Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";

export const GROUP_MAPPINGS_LIST: ListDefinition = {
  id: "admin.access.group.mappings",
  fields: [],
  // The server matches the role slug or name.
  searchPlaceholder: "Search mappings by role…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [10, 25, 50],
};

/** `astroliftGroupRoleMappingsPage` variables for one group. */
export function groupMappingsVariables(
  externalId: string,
  state: { q: string; page: number; pageSize: number }
) {
  return {
    groupExternalId: externalId,
    search: state.q.trim() || null,
    page: Math.max(1, state.page),
    pageSize: state.pageSize,
  };
}
