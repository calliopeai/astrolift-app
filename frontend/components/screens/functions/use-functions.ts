"use client";

import { useListState } from "@/components/list/use-list-state";
import { useAreaWorkloads } from "@/components/screens/workloads/use-area-workloads";

import { FUNCTIONS_LIST, selectFunctions } from "./functions-list";

/**
 * Agents › Functions on URL list state: the area's workload walk narrowed to
 * functions. The data half of FunctionsScreen.
 */
export function useFunctions() {
  const list = useListState(FUNCTIONS_LIST);
  const { state } = list;
  const { workloads, loading, stale, error, onRetry } = useAreaWorkloads();
  const { rows, totalCount } = selectFunctions(workloads, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
  return { list, rows, totalCount, loading, stale, error, onRetry };
}

export type FunctionsState = ReturnType<typeof useFunctions>;
