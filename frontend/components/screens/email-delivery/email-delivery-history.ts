import type { ListDefinition } from "@/components/list/list-state";
/** Exact-service history has cursor paging, with no server search or requester filter. */
export function emailHistoryDefinition(label: string): ListDefinition {
  return {
    id: "email.deliveryTests",
    fields: [],
    searchPlaceholder: label,
    searchable: false,
    defaultSort: [],
    views: [{ key: "all", label, filters: {} }],
    paging: "cursor",
    pageSizes: [10, 25, 50],
    defaultPageSize: 25,
  };
}
