import type { ListDefinition } from "@/components/list/list-state";

/**
 * Admin › Policies (spec 44 §5.1). `astroliftPoliciesPage` takes `search`,
 * `limit` and `after`: it cannot filter by effect or scope, cannot answer
 * "Mine" (created by me), and offers no page numbers, so the list declares
 * one view, no filter fields and cursor paging until the query grows them.
 * Its seek key is newest first.
 */
export const POLICIES_LIST: ListDefinition = {
  id: "admin.policies",
  fields: [],
  searchPlaceholder: "Search policies…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};
