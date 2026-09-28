"use client";

import { useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { useClusterActions } from "./use-cluster-actions";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

const POLL_INTERVAL_MS = 4000;

/**
 * One cluster out of the fleet list, polled while its management workflow
 * is in flight, and the lifecycle actions its header offers. The data half
 * of ClusterDetail.
 */
export function useClusterDetail(slug: string) {
  // cache-and-network ensures a live fetch with the correct
  // X-Astrolift-Organization header on first client mount. The default
  // cache-first policy reads the stale SSR result (no org header) and
  // leaves this page showing "not found" while the breadcrumb already
  // reflects the cluster slug from the URL.
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_CLUSTERS, {
    fetchPolicy: "cache-and-network",
  });
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);
  const lifecycle = cluster?.lifecycle ?? "registered";

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  const router = useRouter();
  const actions = useClusterActions();
  // The cluster is gone once unregistered; land on the list it left.
  async function onUnregister(c: AstroliftTenantCluster) {
    await actions.onUnregister(c);
    router.push("/clusters");
  }

  return { slug, cluster, loading, ...actions, onUnregister };
}
