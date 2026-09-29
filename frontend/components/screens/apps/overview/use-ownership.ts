"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_APP_TEAM_ACCESSES } from "@/graphql/registry/registry.queries";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

interface Resp {
  astroliftAppTeamAccesses: AstroliftAppTeamAccess[];
}

/**
 * The teams holding a grant on this app, for the overview's Teams & project
 * panel. The same flat query the Settings teams card reads, so the cache is
 * shared. The data half of OwnershipPanel.
 */
export function useOwnership(appSlug: string) {
  const { data, loading, error, refetch } = useQuery<Resp>(LIST_APP_TEAM_ACCESSES, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  return {
    loading: loading && !data,
    accesses: data?.astroliftAppTeamAccesses ?? [],
    /** Only when there is nothing cached to show. */
    error: data ? null : (error?.message ?? null),
    onRetry: () => void refetch(),
  };
}
