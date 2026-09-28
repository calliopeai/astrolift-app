"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  DECOMMISSION_CLUSTER,
  LIST_CLUSTERS,
  REFRESH_CLUSTER_MANAGEMENT,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import type { ClusterWithHeartbeat, Lifecycle } from "./types";

interface Resp {
  astroliftClusters: ClusterWithHeartbeat[];
}

const POLL_INTERVAL_MS = 4000;

/**
 * The cluster behind the settings tab and every lifecycle mutation it
 * drives (Bring into management, Refresh, Re-run preflight, Force
 * retrigger, Decommission). Polls while the management workflow runs.
 * The data half of ClusterSettingsScreen.
 */
export function useClusterSettings(slug: string) {
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug) ?? null;
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [decommission, { loading: decommissioning }] = useMutation<{
    decommissionCluster: MutationResult<AstroliftTenantCluster>;
  }>(DECOMMISSION_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the decommission was accepted (close the dialog). */
  async function onDecommission(deleteCloudInfra: boolean): Promise<boolean> {
    if (!cluster) return false;
    const { data } = await decommission({
      variables: { input: { clusterId: cluster.id, deleteCloudInfra } },
    });
    if (data?.decommissionCluster.ok) {
      toast.success(
        deleteCloudInfra
          ? `Decommissioning ${cluster.slug} + deleting cloud infrastructure`
          : `Decommissioning ${cluster.slug} (cluster left running)`
      );
      return true;
    }
    toast.error(data?.decommissionCluster.errors?.[0]?.message ?? "Failed");
    return false;
  }

  async function onBring() {
    if (!cluster) return;
    const { data } = await bring({ variables: { input: { clusterId: cluster.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${cluster.slug} into management`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function onRefresh(forcePreflight: boolean) {
    if (!cluster) return;
    const { data } = await refresh({
      variables: { input: { clusterId: cluster.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight
          ? `Refreshing ${cluster.slug} (full preflight)`
          : `Refreshing ${cluster.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  return {
    cluster,
    loading,
    lifecycle,
    bringing,
    refreshing,
    decommissioning,
    onBring,
    onRefresh,
    onDecommission,
  };
}
