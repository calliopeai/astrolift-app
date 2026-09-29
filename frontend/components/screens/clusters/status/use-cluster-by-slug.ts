"use client";

import { useQuery } from "@apollo/client/react";

import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftCluster: AstroliftTenantCluster | null;
}

/**
 * One cluster by slug, read on its own (#2150): finding it in the fleet
 * list made every cluster past the 200th "not found".
 */
export function useClusterBySlug(slug: string) {
  // cache-and-network: the SSR preload runs before the org cookie is set
  // and answers null; a cache-first read would keep that answer.
  const { data, loading, error, refetch } = useQuery<Resp>(GET_CLUSTER, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  const cluster = data?.astroliftCluster ?? null;
  return { cluster, loading, error: error?.message ?? null, refetch: () => void refetch() };
}
