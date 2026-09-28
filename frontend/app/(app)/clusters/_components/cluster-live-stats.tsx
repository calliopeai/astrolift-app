"use client";

import { ClusterLiveStats } from "@/components/screens/clusters/list/ClusterLiveStats";
import { useClusterLiveStats } from "@/components/screens/clusters/list/use-cluster-live-stats";

/** The live stats row wired to one cluster; mounted only once the cluster is known. */
export function ClusterLiveStatsContainer({ clusterId }: { clusterId: string }) {
  return <ClusterLiveStats {...useClusterLiveStats(clusterId)} />;
}
