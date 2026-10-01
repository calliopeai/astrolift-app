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

interface LifecycleResp {
  bringClusterIntoManagement?: MutationResult<AstroliftTenantCluster>;
  refreshClusterManagement?: MutationResult<AstroliftTenantCluster>;
  decommissionCluster?: MutationResult<AstroliftTenantCluster>;
}

type Action = "bring" | "refresh" | "decommission";
const POLL_INTERVAL_MS = 4000;

export function useClusterSettings(slug: string) {
  const sourceT = useTranslations("clusterSettings.source");
  const t = useTranslations("clusterSettings.lifecycle");
  const {
    data,
    previousData,
    error: readError,
    refetch,
    loading,
    startPolling,
    stopPolling,
  } = useQuery<Resp>(GET_CLUSTER, {
    variables: { slug },
    // A cache merge can replace missing network fields with an older confirmed read.
    fetchPolicy: "no-cache",
    notifyOnNetworkStatusChange: true,
  });
  const perms = useMyPermissions();
  const permissionPending = perms.loading && perms.granted.size === 0;
  const allow = (p: AstroliftPermission) => permissionPending || perms.can(p);
  const access: ClusterSettingsAccess = {
    manage: allow("cluster.manage"),
    update: allow("cluster.update"),
    users: allow("cluster.users"),
    unregister: allow("cluster.unregister"),
  };
  const observedCluster = data?.astroliftCluster;
  const lastCluster = previousData?.astroliftCluster;
  const cluster =
    observedCluster?.slug === slug
      ? observedCluster
      : (readError || loading || observedCluster === undefined) && lastCluster?.slug === slug
        ? lastCluster
        : null;
  const error =
    readError?.message ??
    (!loading &&
    (observedCluster === undefined || (observedCluster !== null && observedCluster.slug !== slug))
      ? sourceT("unknown")
      : null);
  const observed = !!cluster && !loading && !error;
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";
  const fingerprint = JSON.stringify([slug, cluster, observed, access.manage, access.unregister]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  const [pending, setPending] = React.useState<{ context: object; action: Action } | null>(null);
  const running = React.useRef<object | null>(null);

  async function onRetry() {
    try {
      await refetch();
    } catch {
      /* The query retains its diagnostic. */
    }
  }

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  const [bring] = useMutation<LifecycleResp>(BRING_CLUSTER_INTO_MANAGEMENT, {
    fetchPolicy: "no-cache",
  });
  const [refresh] = useMutation<LifecycleResp>(REFRESH_CLUSTER_MANAGEMENT, {
    fetchPolicy: "no-cache",
  });
  const [decommission] = useMutation<LifecycleResp>(DECOMMISSION_CLUSTER, {
    fetchPolicy: "no-cache",
  });

  async function request(action: Action, flag = false) {
    const allowed = action === "decommission" ? access.unregister : access.manage;
    if (current.current !== context || !observed || !cluster || !allowed) {
      throw new Error(t("sourceChanged"));
    }
    if (running.current === context) throw new Error(t("pending"));
    running.current = context;
    setPending({ context, action });
    try {
      const operation = { bring, refresh, decommission }[action];
      const input = {
        clusterId: cluster.id,
        ...(action === "refresh" ? { forcePreflight: flag } : {}),
        ...(action === "decommission" ? { deleteCloudInfra: flag } : {}),
      };
      const field = {
        bring: "bringClusterIntoManagement",
        refresh: "refreshClusterManagement",
        decommission: "decommissionCluster",
      }[action] as keyof LifecycleResp;
      const result = (await operation({ variables: { input } })).data?.[field];
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message || t("failed"));
      const target = t("target", { clusterId: cluster.id });
      toast.success(
        t(
          action === "bring"
            ? "manageRequested"
            : action === "refresh"
              ? flag
                ? "fullRequested"
                : "refreshRequested"
              : flag
                ? "deleteRequested"
                : "retireRequested",
          { slug: cluster.slug }
        ) +
          " " +
          target
      );
      if (current.current !== context) return;
      try {
        const next = await refetch();
        if (
          next.error ||
          !next.data?.astroliftCluster ||
          next.data.astroliftCluster.id !== cluster.id ||
          next.data.astroliftCluster.slug !== cluster.slug
        ) {
          toast.warning(t("acceptedRefreshFailed", { slug: cluster.slug }) + " " + target);
        }
      } catch {
        toast.warning(t("acceptedRefreshFailed", { slug: cluster.slug }) + " " + target);
      }
    } finally {
      if (running.current === context) running.current = null;
      setPending((active) => (active?.context === context ? null : active));
    }
  }

  async function onBring() {
    try {
      await request("bring");
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("failed"));
    }
  }

  async function onRefresh(forcePreflight: boolean) {
    try {
      await request("refresh", forcePreflight);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("failed"));
    }
  }

  return {
    cluster,
    loading,
    error,
    onRetry,
    readOnly: !observed,
    lifecycle,
    bringing: pending?.context === context && pending.action === "bring",
    refreshing: pending?.context === context && pending.action === "refresh",
    decommissioning: pending?.context === context && pending.action === "decommission",
    onBring,
    onRefresh,
    onDecommission: (deleteCloudInfra: boolean) => request("decommission", deleteCloudInfra),
    access,
  };
}
