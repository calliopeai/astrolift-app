"use client";

import { ClusterTabFrame } from "@/components/screens/clusters/status/ClusterTabFrame";
import { useClusterBySlug } from "@/components/screens/clusters/status/use-cluster-by-slug";

import { ClusterHealthContainer } from "../../_components/cluster-status-tabs";
import { ClusterTabs } from "../components/cluster-tabs";

/**
 * Cluster health tab — pod-phase rollup + recent warning events +
 * per-Deployment readiness. Sourced from the driver's kube API
 * client; polls every 30s.
 */
export function ClusterHealthClient({ slug }: { slug: string }) {
  const { cluster, loading } = useClusterBySlug(slug);
  return (
    <ClusterTabFrame
      slug={slug}
      cluster={cluster}
      loading={loading}
      loadingTitle="Cluster health"
      tabLabel="Health"
      tabs={<ClusterTabs slug={slug} active="health" />}
    >
      {cluster ? <ClusterHealthContainer clusterId={cluster.id} /> : null}
    </ClusterTabFrame>
  );
}
