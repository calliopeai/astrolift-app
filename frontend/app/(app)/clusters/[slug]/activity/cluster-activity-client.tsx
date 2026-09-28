"use client";

import { ClusterTabFrame } from "@/components/screens/clusters/status/ClusterTabFrame";
import { useClusterBySlug } from "@/components/screens/clusters/status/use-cluster-by-slug";

import { ClusterActivityContainer } from "../../_components/cluster-status-tabs";
import { ClusterTabs } from "@/components/screens/clusters/list/ClusterTabs";

/**
 * Cluster activity tab — recent Temporal workflow runs targeting
 * this cluster + the lifecycle audit timeline pulled from
 * MutationAuditLog.
 */
export function ClusterActivityClient({ slug }: { slug: string }) {
  const { cluster, loading } = useClusterBySlug(slug);
  return (
    <ClusterTabFrame
      slug={slug}
      cluster={cluster}
      loading={loading}
      loadingTitle="Cluster activity"
      tabLabel="Activity"
      tabs={<ClusterTabs slug={slug} active="activity" />}
    >
      {cluster ? <ClusterActivityContainer clusterId={cluster.id} /> : null}
    </ClusterTabFrame>
  );
}
