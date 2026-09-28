"use client";

import { useQuery } from "@apollo/client/react";

import type { ListModelEndpointsQuery } from "@/graphql/__generated__/operations";
import { LIST_MODEL_ENDPOINTS } from "@/graphql/models/models.queries";

/** The org's model endpoints (#2040). The data half of ModelsScreen. */
export function useModels() {
  const { data, loading, error, refetch } = useQuery<ListModelEndpointsQuery>(LIST_MODEL_ENDPOINTS);
  return {
    models: data?.astroliftModelEndpoints ?? [],
    loading,
    error,
    refetch: () => {
      void refetch();
    },
  };
}
