/**
 * The app's deploy tokens as a list (spec 44 §5.1): one cursor page of
 * `astroliftAppDeployTokensPage` at a time. The field searches the name,
 * last 4, IP and user agent, and takes no filter and no sort, so the list
 * declares no fields and no sortable column; state and order filters wait on
 * the backend. Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";

export const APP_DEPLOY_TOKENS_LIST: ListDefinition = {
  id: "apps.access.tokens",
  fields: [],
  searchPlaceholder: "Search tokens by name, last 4, IP or user agent…",
  defaultSort: [],
  // A token has no owner of its own, so there is no Mine: one view, and the
  // list draws no view picker.
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};
