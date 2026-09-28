"use client";

import { ClustersList } from "@/components/screens/clusters/list/ClustersList";
import { useClustersList } from "@/components/screens/clusters/list/use-clusters-list";

import { RegisterClusterDialog } from "./register-cluster-dialog";

export function ClustersClient() {
  return (
    <ClustersList
      {...useClustersList()}
      renderRegisterDialog={(props) => <RegisterClusterDialog {...props} />}
    />
  );
}
