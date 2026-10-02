"use client";

import type { CursorTableController } from "@/components/data-table";
import type { ListDefinition, ListStateController } from "./list-state";
import { useLocalListState } from "./use-list-state";

/** ListPage chrome around the existing server cursor walk; no client pagination. */
export function useCursorTableList<T>(
  controller: CursorTableController<T>,
  options: {
    id: string;
    label: string;
    searchPlaceholder: string;
    searchable?: boolean;
  }
): ListStateController {
  const definition: ListDefinition = {
    id: options.id,
    fields: [],
    searchPlaceholder: options.searchPlaceholder,
    searchable: options.searchable ?? controller.searchEnabled,
    defaultSort: [],
    // These owner-scoped endpoints have no personal-ownership filter. Do not
    // fabricate a Mine view that merely returns the same project resources.
    views: [{ key: "all", label: options.label, filters: {} }],
    paging: "cursor",
    pageSizes: [10, 25, 50, 100],
    defaultPageSize: controller.pageSize,
  };
  const local = useLocalListState(definition);
  return {
    ...local,
    state: {
      ...local.state,
      q: controller.search,
      page: controller.pageIndex + 1,
      pageSize: controller.pageSize,
      after: controller.after ?? null,
      sort: [],
    },
    filters: {},
    isFiltered: controller.isFiltered,
    setSearch: controller.setSearch,
    applySearch: (search) => controller.setSearch(search),
    clearFilters: controller.clearFilters,
    older: controller.next,
    newer: controller.prev,
    hasNewer: controller.hasPrev,
    setPageSize: controller.setPageSize,
  };
}
