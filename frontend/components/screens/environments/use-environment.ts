"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

/**
 * One environment by id. Reuses the global LIST_ENVIRONMENTS query (no
 * singular query exists), a cache hit when navigated from the global
 * /environments list. The data half of EnvironmentDetail.
 */
export function useEnvironment(id: string) {
  const { data, loading, error, refetch } = useQuery<Resp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const environment = React.useMemo(
    () => (data?.astroliftEnvironments ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return {
    loading: loading && !data,
    environment,
    error: error && !data ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}

export type EnvironmentState = ReturnType<typeof useEnvironment>;
