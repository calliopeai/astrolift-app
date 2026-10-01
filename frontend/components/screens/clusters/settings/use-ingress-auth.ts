"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  COGNITO_USER_POOL_CLIENTS,
  COGNITO_USER_POOLS,
  RECONCILE_CLUSTER_INGRESSES,
  UPDATE_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftTenantCluster,
  ReconcileClusterIngressesResult,
} from "@/graphql/clusters/clusters.types";
import type {
  CognitoUserPoolClientsQuery,
  CognitoUserPoolsQuery,
} from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { type IngressAuthConfig, isAlbAuthConfig, poolIdFromArn } from "./types";

function sourceIdentity(config: IngressAuthConfig | null) {
  return JSON.stringify([
    config?.user_pool_arn,
    config?.user_pool_client_id,
    config?.user_pool_domain,
  ]);
}

export function useIngressAuth(cluster: AstroliftTenantCluster) {
  const t = useTranslations("clusterSettings.ingressAuth");
  const existing = isAlbAuthConfig(cluster.albAuthConfig) ? cluster.albAuthConfig : null;
  const source = sourceIdentity(existing);
  const target = JSON.stringify([
    cluster.id,
    cluster.providerPluginSlug,
    cluster.ingressClass,
    cluster.region,
  ]);
  const sourceKey = JSON.stringify([target, source]);
  const lease = React.useMemo(() => ({ target }), [target]);
  const [form, setForm] = React.useState({
    key: sourceKey,
    epoch: 0,
    editing: false,
    poolId: poolIdFromArn(existing?.user_pool_arn ?? ""),
  });
  if (form.key !== sourceKey)
    setForm({
      key: sourceKey,
      epoch: form.epoch + 1,
      editing: false,
      poolId: poolIdFromArn(existing?.user_pool_arn ?? ""),
    });
  const epoch = form.key === sourceKey ? form.epoch : form.epoch + 1;
  const current = React.useRef<{ lease: typeof lease; source: string; epoch: number } | null>({
    lease,
    source,
    epoch,
  });
  React.useLayoutEffect(() => {
    current.current = { lease, source, epoch };
    return () => {
      current.current = null;
    };
  }, [lease, source, epoch]);
  const editing = form.key === sourceKey && form.editing;
  const poolId =
    form.key === sourceKey ? form.poolId : poolIdFromArn(existing?.user_pool_arn ?? "");
  const isAws = cluster.providerPluginSlug === "aws";
  const poolsQuery = useQuery<CognitoUserPoolsQuery>(COGNITO_USER_POOLS, {
    variables: { clusterId: cluster.id },
    skip: !editing || !isAws,
    fetchPolicy: "cache-and-network",
  });
  const clientsQuery = useQuery<CognitoUserPoolClientsQuery>(COGNITO_USER_POOL_CLIENTS, {
    variables: { clusterId: cluster.id, poolId },
    skip: !editing || !isAws || !poolId,
    fetchPolicy: "cache-and-network",
  });
  const [update, { loading: updating }] = useMutation<{
    updateTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(UPDATE_TENANT_CLUSTER, {
    refetchQueries: (result) => (result.data?.updateTenantCluster.ok ? ["GetCluster"] : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });
  const [reconcile, { loading: reconciling }] = useMutation<{
    reconcileClusterIngresses: MutationResult<ReconcileClusterIngressesResult>;
  }>(RECONCILE_CLUSTER_INGRESSES);
  const [pending, setPending] = React.useState(false);
  const pendingRef = React.useRef(false);

  async function persistAndReconcile(config: IngressAuthConfig | null): Promise<boolean> {
    if (
      pendingRef.current ||
      current.current?.lease !== lease ||
      current.current.source !== source ||
      current.current.epoch !== epoch
    )
      return false;
    pendingRef.current = true;
    setPending(true);
    try {
      let updateData;
      try {
        ({ data: updateData } = await update({
          variables: { input: { id: cluster.id, albAuthConfig: config } },
        }));
      } catch (error) {
        toast.error(error instanceof Error && error.message ? error.message : t("saveFailed"));
        return false;
      }
      if (!updateData?.updateTenantCluster.ok) {
        toast.error(updateData?.updateTenantCluster.errors?.[0]?.message ?? t("saveFailed"));
        return false;
      }
      toast.success(t("saved"));
      // The saved config is committed even when the next stage is unavailable.
      if (
        current.current?.lease !== lease ||
        !(
          (current.current.epoch === epoch && current.current.source === source) ||
          (current.current.epoch === epoch + 1 &&
            sourceIdentity(config) !== source &&
            current.current.source === sourceIdentity(config))
        )
      ) {
        toast.warning(t("savedRolloutUnconfirmed"));
        return true;
      }
      try {
        const { data } = await reconcile({ variables: { input: { clusterId: cluster.id } } });
        const result = data?.reconcileClusterIngresses;
        if (!result?.ok) {
          toast.warning(
            t("savedReconcileFailed", {
              reason: result?.errors?.[0]?.message ?? t("reconcileFailed"),
            })
          );
          return true;
        }
        const count = result.data?.reconciledCount;
        if (typeof count !== "number" || !Number.isInteger(count) || count < 0) {
          toast.warning(t("savedRolloutUnconfirmed"));
          return true;
        }
        toast.success(t(config ? "applied" : "removed", { count }));
        const failures = result.data?.errors ?? [];
        if (failures.length) toast.warning(t("partial", { count: failures.length }));
        const skipped = result.data?.skippedCount;
        if (typeof skipped === "number" && skipped > 0)
          toast.warning(t("skipped", { count: skipped }));
      } catch (error) {
        toast.warning(
          t("savedReconcileFailed", {
            reason: error instanceof Error && error.message ? error.message : t("reconcileFailed"),
          })
        );
      }
      return true;
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }
  function setEditing(editing: boolean) {
    if (
      current.current?.lease === lease &&
      current.current.source === source &&
      current.current.epoch === epoch
    )
      setForm({
        key: sourceKey,
        epoch,
        editing,
        poolId: poolIdFromArn(existing?.user_pool_arn ?? ""),
      });
  }
  async function onDisable() {
    if (await persistAndReconcile(null)) setEditing(false);
  }
  async function onSaveAndApply(config: IngressAuthConfig | null) {
    if (config === null) {
      toast.error(t("required"));
      return;
    }
    if (await persistAndReconcile(config)) setEditing(false);
  }
  return {
    sourceKey,
    providerPluginSlug: cluster.providerPluginSlug,
    ingressClass: cluster.ingressClass,
    existing,
    isAws,
    editing,
    poolId,
    onPoolIdChange: (poolId: string) => {
      if (
        current.current?.lease === lease &&
        current.current.source === source &&
        current.current.epoch === epoch
      )
        setForm((value) => (value.key === sourceKey ? { ...value, poolId } : value));
    },
    pools: poolsQuery.data?.astroliftCognitoUserPools ?? [],
    poolsLoading: poolsQuery.loading,
    poolsErrored: !!poolsQuery.error,
    poolsError: poolsQuery.error?.message ?? null,
    onRetryPools: async () => {
      if (current.current?.lease !== lease || current.current.epoch !== epoch) return;
      try {
        await poolsQuery.refetch();
      } catch {
        /* Query error remains visible. */
      }
    },
    clients: clientsQuery.data?.astroliftCognitoUserPoolClients ?? [],
    clientsLoading: clientsQuery.loading,
    clientsError: clientsQuery.error?.message ?? null,
    onRetryClients: async () => {
      if (current.current?.lease !== lease || current.current.epoch !== epoch) return;
      try {
        await clientsQuery.refetch();
      } catch {
        /* Query error remains visible. */
      }
    },
    busy: updating || reconciling || pending,
    reconciling,
    onOpenForm: () => setEditing(true),
    onCancelEdit: () => setEditing(false),
    onDisable,
    onApply: async () => {
      await persistAndReconcile(existing);
    },
    onSaveAndApply,
  };
}
