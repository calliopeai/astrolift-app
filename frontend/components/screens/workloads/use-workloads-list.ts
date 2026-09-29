"use client";

import { useListState } from "@/components/list/use-list-state";

import { useAreaWorkloads } from "./use-area-workloads";
import { WORKLOADS_LIST, workloadsPageVariables } from "./workloads-list";

/** Agents › Workloads on URL list state. The data half of WorkloadsScreen. */
export function useWorkloadsList() {
  const list = useListState(WORKLOADS_LIST);
  const { state } = list;
  const page = useAreaWorkloads(
    workloadsPageVariables({
      q: state.q,
      filters: list.filters,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    })
  );
  return { list, ...page };
}

export type WorkloadsListState = ReturnType<typeof useWorkloadsList>;
