/**
 * An app's managed services as a list (spec 44 §5.1): one cursor page of
 * `astroliftManagedServicesPage` at a time. The field searches the name,
 * kind, variant and environment, and takes no filter and no sort, so the
 * list declares no fields and no sortable column; kind and status filters
 * wait on the backend. Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";

export const APP_MANAGED_SERVICES_LIST: ListDefinition = {
  id: "apps.workloads.managed-services",
  fields: [],
  searchPlaceholder: "Search by name, kind, variant, or environment…",
  defaultSort: [],
  // A service has no owner of its own, so there is no Mine: one view.
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};
