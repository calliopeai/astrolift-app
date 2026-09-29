"use client";

import { useListState } from "@/components/list/use-list-state";

import { useAreaWorkloads } from "./use-area-workloads";
import { selectWorkloads, WORKLOADS_LIST } from "./workloads-list";

/** Agents › Workloads on URL list state. The data half of WorkloadsScreen. */
export function useWorkloadsList() {
  const list = useListState(WORKLOADS_LIST);
  const { state } = list;
  const { workloads, loading, stale, error, onRetry } = useAreaWorkloads();
  const { rows, totalCount } = selectWorkloads(workloads, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
  return { list, rows, totalCount, loading, stale, error, onRetry };
}

export type WorkloadsListState = ReturnType<typeof useWorkloadsList>;
