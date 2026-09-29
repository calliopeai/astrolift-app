"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import type { ListModelEndpointsQuery } from "@/graphql/__generated__/operations";
import { LIST_MODEL_ENDPOINTS } from "@/graphql/models/models.queries";

import { MODELS_LIST, selectModels } from "./models-list";

/** The org's model endpoints (#2040). One read the list and the detail share. */
export function useModelEndpoints() {
  const { data, previousData, loading, error, refetch } =
    useQuery<ListModelEndpointsQuery>(LIST_MODEL_ENDPOINTS);
  const models = (data ?? previousData)?.astroliftModelEndpoints ?? [];
  return {
    models,
    loading: loading && models.length === 0 && !error,
    error: error ? { message: error.message } : null,
    refetch: () => {
      void refetch();
    },
  };
}

/** The Models list on URL list state. The data half of ModelsScreen. */
export function useModels() {
  const list = useListState(MODELS_LIST);
  const { state } = list;
  const { models, loading, error, refetch } = useModelEndpoints();
  const { rows, totalCount } = selectModels(models, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
  return { list, rows, totalCount, loading, error, onRetry: refetch };
}

export type ModelsState = ReturnType<typeof useModels>;
