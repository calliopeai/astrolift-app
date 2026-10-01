"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  DECOMMISSION_CLUSTER,
  GET_CLUSTER,
  REFRESH_CLUSTER_MANAGEMENT,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { type AstroliftPermission, useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { ClusterSettingsAccess, ClusterWithHeartbeat, Lifecycle } from "./types";

interface Resp {
  astroliftCluster: ClusterWithHeartbeat | null;
}

const POLL_INTERVAL_MS = 4000;

// The cluster on screen, by operation name: its variables carry the slug.
const REFETCH_CLUSTER = "GetCluster";

/**
 * The cluster behind the settings tab and every lifecycle mutation it
 * drives (Bring into management, Refresh, Re-run preflight, Force
 * retrigger, Decommission). Polls while the management workflow runs.
 * Also what the viewer may change, per section. The data half of
 * ClusterSettingsScreen.
 */
export function useClusterSettings(slug: string) {
  const t = useTranslations("clusterSettings.source");
  // One cluster by slug (#2150); the SSR preload answers before the org
  // cookie is set, so the first client read goes to the network.
  const {
    data,
    error: readError,
    refetch,
    loading,
    startPolling,
    stopPolling,
  } = useQuery<Resp>(GET_CLUSTER, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  const perms = useMyPermissions();
  // Optimistic while the permission set loads, as `Can` is: the backend
  // still refuses, and the fields do not flash disabled on first paint.
  const pending = perms.loading && perms.granted.size === 0;
  const allow = (p: AstroliftPermission) => pending || perms.can(p);
  const access: ClusterSettingsAccess = {
    manage: allow("cluster.manage"),
    update: allow("cluster.update"),
    users: allow("cluster.users"),
    unregister: allow("cluster.unregister"),
  };
  const observedCluster = data?.astroliftCluster;
  const cluster = observedCluster?.slug === slug ? observedCluster : null;
  const error =
    readError?.message ??
    (!loading && (observedCluster === undefined || (observedCluster !== null && !cluster))
      ? t("unknown")
      : null);

  async function onRetry() {
    try {
      await refetch();
    } catch {
      /* The query retains its diagnostic. */
    }
  }
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
    refetchQueries: [REFETCH_CLUSTER],
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: [REFETCH_CLUSTER],
    awaitRefetchQueries: true,
  });
  const [decommission, { loading: decommissioning }] = useMutation<{
    decommissionCluster: MutationResult<AstroliftTenantCluster>;
  }>(DECOMMISSION_CLUSTER, {
    refetchQueries: [REFETCH_CLUSTER],
    awaitRefetchQueries: true,
  });

  /** Throws on refusal, so the confirm dialog stays open with the error. */
  async function onDecommission(deleteCloudInfra: boolean): Promise<void> {
    if (!cluster) return;
    const { data } = await decommission({
      variables: { input: { clusterId: cluster.id, deleteCloudInfra } },
    });
    if (data?.decommissionCluster.ok) {
      toast.success(
        deleteCloudInfra
          ? `Decommissioning ${cluster.slug} + deleting cloud infrastructure`
          : `Decommissioning ${cluster.slug} (cluster left running)`
      );
      return;
    }
    throw new Error(data?.decommissionCluster.errors?.[0]?.message ?? "Failed");
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
    error,
    onRetry,
    lifecycle,
    bringing,
    refreshing,
    decommissioning,
    onBring,
    onRefresh,
    onDecommission,
    access,
  };
}
