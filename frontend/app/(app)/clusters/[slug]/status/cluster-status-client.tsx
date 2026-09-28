"use client";

import { ClusterTabFrame } from "@/components/screens/clusters/status/ClusterTabFrame";
import { useClusterBySlug } from "@/components/screens/clusters/status/use-cluster-by-slug";

import { ClusterStatusContainer } from "../../_components/cluster-status-tabs";
import { ClusterTabs } from "@/components/screens/clusters/list/ClusterTabs";

/** Cluster status tab (#68, #808): connection, saturation, health, workflows, lifecycle. */
export function ClusterStatusClient({ slug }: { slug: string }) {
  const { cluster, loading } = useClusterBySlug(slug);
  return (
    <ClusterTabFrame
      slug={slug}
      cluster={cluster}
      loading={loading}
      loadingTitle="Cluster status"
      tabLabel="Status"
      tabs={<ClusterTabs slug={slug} active="status" />}
    >
      {cluster ? <ClusterStatusContainer clusterId={cluster.id} slug={slug} /> : null}
    </ClusterTabFrame>
  );
}
