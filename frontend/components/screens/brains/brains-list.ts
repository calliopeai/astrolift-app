/**
 * Apps › Semantic layers (#2320): the brain apps the viewer can read. The
 * server answers the search; the list is short and unpaged. Pure.
 */
import type { CopiedListDefinition } from "@/components/list/list-state";

export const BRAINS_LIST: CopiedListDefinition = {
  id: "apps.brains",
  copy: "brains",
  fields: [],
  searchPlaceholder: "Search names and slugs…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [200],
};
