"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { GET_MODEL_ENDPOINT, LIST_MODEL_ENDPOINTS_PAGE } from "@/graphql/models/models.queries";

import { type ModelEndpoint, MODELS_LIST, modelEndpointsPageVariables } from "./models-list";

interface ModelEndpointsPageResp {
  astroliftModelEndpointsPage: { items: ModelEndpoint[]; totalCount: number | null };
}
interface ModelEndpointResp {
  astroliftModelEndpoint: ModelEndpoint | null;
}

/**
 * The Models list: URL list state in, one numbered page of
 * `astroliftModelEndpointsPage` out (#2155). The server filters, searches,
 * sorts and counts. The data half of ModelsScreen.
 */
export function useModels() {
  const list = useListState(MODELS_LIST);
  const { state } = list;
  const variables = modelEndpointsPageVariables({
    q: state.q,
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
  const query = useQuery<ModelEndpointsPageResp>(LIST_MODEL_ENDPOINTS_PAGE, {
    variables: variables ?? undefined,
    skip: variables === null,
    fetchPolicy: "cache-and-network",
  });
  // A Serving chip outside its view matches nothing; nothing was asked.
  const data = variables === null ? undefined : (query.data ?? query.previousData);
  const rows = data?.astroliftModelEndpointsPage.items ?? [];
  return {
    list,
    rows,
    totalCount: data?.astroliftModelEndpointsPage.totalCount ?? rows.length,
    loading: variables !== null && query.loading && !data && !query.error,
    // Rows on screen answer the previous list state while the next loads.
    stale: variables !== null && query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
  };
}

export type ModelsState = ReturnType<typeof useModels>;

/** One model endpoint by id (`astroliftModelEndpoint`, #2155). */
export function useModelEndpoint(id: string) {
  const { data, loading, error, refetch } = useQuery<ModelEndpointResp>(GET_MODEL_ENDPOINT, {
    variables: { id },
    skip: !id,
    fetchPolicy: "cache-and-network",
  });
  return {
    model: data?.astroliftModelEndpoint ?? null,
    loading: loading && !data && !error,
    error: error ? { message: error.message } : null,
    refetch: () => {
      void refetch();
    },
  };
}
