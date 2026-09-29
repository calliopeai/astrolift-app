"use client";

import { useModelEndpoint } from "./use-models";

/**
 * One model endpoint, read by id with `astroliftModelEndpoint` (#2155), so
 * an endpoint past the list's first page resolves. The data half of
 * ModelDetailScreen.
 */
export function useModelDetail(id: string) {
  const { model, loading, error, refetch } = useModelEndpoint(id);
  return { model, loading, error, onRetry: refetch };
}
