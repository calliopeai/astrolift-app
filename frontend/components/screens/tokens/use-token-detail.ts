"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import type { AstroliftApiToken } from "@/graphql/identity/identity.types";

interface Resp {
  astroliftApiTokens: AstroliftApiToken[];
}

/**
 * One API token (#1106). Reuses LIST_API_TOKENS (no singular query exists).
 * The data half of TokenDetailScreen.
 */
export function useTokenDetail(id: string) {
  const query = useQuery<Resp>(LIST_API_TOKENS, {
    fetchPolicy: "cache-and-network",
  });

  const data = query.data ?? query.previousData;
  const token = React.useMemo(
    () => (data?.astroliftApiTokens ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return {
    id,
    token,
    loading: query.loading && !data,
    error: query.error ?? null,
    onRetry: async () => {
      try {
        await query.refetch();
      } catch {
        /* Apollo retains the actual read error. */
      }
    },
  };
}
