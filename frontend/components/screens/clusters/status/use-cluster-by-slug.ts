"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

/** One cluster out of the (preloaded) cluster list, by slug. */
export function useClusterBySlug(slug: string) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug) ?? null;
  return { cluster, loading };
}
