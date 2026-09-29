"use client";

import { ClusterDetail } from "@/components/screens/clusters/list/ClusterDetail";
import { useClusterDetail } from "@/components/screens/clusters/list/use-cluster-detail";

import { ClusterLiveStatsContainer } from "../_components/cluster-live-stats";

export function ClusterDetailClient({ slug }: { slug: string }) {
  return (
    <ClusterDetail
      {...useClusterDetail(slug)}
      renderLiveStats={(clusterId) => <ClusterLiveStatsContainer clusterId={clusterId} />}
    />
  );
}
