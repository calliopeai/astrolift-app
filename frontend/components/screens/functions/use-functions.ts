"use client";

import { useListState } from "@/components/list/use-list-state";
import { useAreaWorkloads } from "@/components/screens/workloads/use-area-workloads";

import { FUNCTIONS_LIST, functionsPageVariables } from "./functions-list";

/**
 * Agents › Functions on URL list state: the area's workload page held to
 * functions. The data half of FunctionsScreen.
 */
export function useFunctions() {
  const list = useListState(FUNCTIONS_LIST);
  const { state } = list;
  const page = useAreaWorkloads(
    functionsPageVariables({
      q: state.q,
      filters: list.filters,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    })
  );
  return { list, ...page };
}

export type FunctionsState = ReturnType<typeof useFunctions>;
