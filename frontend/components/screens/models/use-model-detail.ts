"use client";

import { useModelEndpoints } from "./use-models";

/**
 * One model endpoint. `astroliftModelEndpoints` has no single-item field, so
 * this reads the list the Models page already cached and picks the id out
 * (Leo's page rule 2: no second fetch of the same thing). The data half of
 * ModelDetailScreen.
 */
export function useModelDetail(id: string) {
  const { models, loading, error, refetch } = useModelEndpoints();
  return {
    model: models.find((m) => m.id === id) ?? null,
    loading,
    error,
    onRetry: refetch,
  };
}
