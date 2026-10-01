"use client";

import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";

import { CLUSTER_LIVE_STATE } from "@/graphql/clusters/clusters.queries";
import type { ClusterLiveState } from "@/lib/cluster-heartbeat";

interface LiveStateResp {
  astroliftClusterLiveState: ClusterLiveState | null;
}

const SNAPSHOT_FIELDS = [
  "clusterId",
  "status",
  "lastHeartbeatAt",
  "heartbeatAgeSeconds",
  "heartbeatIntervalSeconds",
  "agentProvisioned",
  "nodeCount",
  "nodeReadyCount",
  "cpuUtilization",
  "memoryUtilization",
  "podTotal",
  "podsByNamespace",
  "appReadiness",
  "ingressIps",
  "agentVersion",
] as const satisfies readonly (keyof ClusterLiveState)[];

function completeSnapshot(value: ClusterLiveState | null | undefined): value is ClusterLiveState {
  return (
    !!value &&
    typeof value.clusterId === "string" &&
    typeof value.status === "string" &&
    SNAPSHOT_FIELDS.every((field) => Object.hasOwn(value, field))
  );
}

/**
 * The persisted keep-alive heartbeat (#808). The one status read that
 * makes no driver / Prometheus / Temporal call, so it is safe to poll
 * while the cluster is offline.
 */
export function useClusterLiveState(clusterId: string) {
  const t = useTranslations("clusterConnection");
  const { data, previousData, loading, error, refetch } = useQuery<LiveStateResp>(
    CLUSTER_LIVE_STATE,
    {
      variables: { clusterId },
      fetchPolicy: "no-cache",
      notifyOnNetworkStatusChange: true,
      pollInterval: 30000,
    }
  );
  const observed = data?.astroliftClusterLiveState;
  const previous = previousData?.astroliftClusterLiveState;
  const state =
    completeSnapshot(observed) && observed.clusterId === clusterId
      ? observed
      : (loading || error || observed === undefined) &&
          completeSnapshot(previous) &&
          previous.clusterId === clusterId
        ? previous
        : null;
  const diagnostic =
    error?.message ??
    (!loading && (!completeSnapshot(observed) || observed.clusterId !== clusterId)
      ? t("unavailable")
      : null);
  return {
    state,
    loading,
    error: diagnostic,
    refetch: () => {
      void refetch().catch(() => undefined);
    },
  };
}
