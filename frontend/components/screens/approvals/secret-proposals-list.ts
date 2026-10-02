import type { ListDefinition } from "@/components/list/list-state";

export const SECRET_PROPOSALS_LIST: ListDefinition = {
  id: "approvals.secret-proposals",
  fields: [],
  searchable: false,
  searchPlaceholder: "",
  defaultSort: [],
  // The API has no caller/proposer filter; a Mine view would misrepresent the queue.
  views: [{ key: "all", label: "Pending", filters: {} }],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};
