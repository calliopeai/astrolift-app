"use client";

import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("clusterSettings.source");
  const { data, previousData, loading, error, refetch } = useQuery<Resp>(GET_CLUSTER, {
    variables: { slug },
    fetchPolicy: "no-cache",
    notifyOnNetworkStatusChange: true,
  });
  const observed = data?.astroliftCluster;
  const previous = previousData?.astroliftCluster;
  const cluster =
    observed?.slug === slug
      ? observed
      : (loading || error || observed === undefined) && previous?.slug === slug
        ? previous
        : null;
  const diagnostic =
    error?.message ??
    (!loading && (observed === undefined || (observed !== null && observed.slug !== slug))
      ? t("unknown")
      : null);
  return {
    cluster,
    loading,
    error: diagnostic,
    refetch: () => void refetch().catch(() => undefined),
  };
}
