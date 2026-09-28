"use client";

import { ClustersList } from "@/components/screens/clusters/list/ClustersList";
import { useClustersList } from "@/components/screens/clusters/list/use-clusters-list";

export function ClustersClient() {
  return <ClustersList {...useClustersList()} />;
}
