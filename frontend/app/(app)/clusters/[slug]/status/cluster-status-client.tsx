"use client";

import { ClusterTabFrame } from "@/components/screens/clusters/status/ClusterTabFrame";
import { useClusterBySlug } from "@/components/screens/clusters/status/use-cluster-by-slug";

import { ClusterStatusContainer } from "../../_components/cluster-status-tabs";

/** Cluster status tab (#68, #808): connection, saturation, health, workflows, lifecycle. */
export function ClusterStatusClient({ slug }: { slug: string }) {
  const { cluster, loading, error, refetch } = useClusterBySlug(slug);
  return (
    <ClusterTabFrame
      slug={slug}
      cluster={cluster}
      loading={loading}
      error={error}
      onRetry={refetch}
      active="status"
    >
      {cluster ? <ClusterStatusContainer clusterId={cluster.id} slug={slug} /> : null}
    </ClusterTabFrame>
  );
}
