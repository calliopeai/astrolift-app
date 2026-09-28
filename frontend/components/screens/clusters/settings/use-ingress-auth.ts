"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  COGNITO_USER_POOL_CLIENTS,
  COGNITO_USER_POOLS,
  LIST_CLUSTERS,
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

import { type IngressAuthConfig, isAlbAuthConfig, poolIdFromArn } from "./types";

/**
 * The per-app ingress auth gate (#851) and its Cognito pickers (#859).
 *
 * `editing` and `poolId` live here rather than in the view because they
 * are query variables: the pool list is fetched only while the edit form
 * is open on an AWS cluster, and the dependent app-client list only once a
 * pool is picked. The data half of IngressAuthView.
 */
export function useIngressAuth(cluster: AstroliftTenantCluster) {
  const existing = isAlbAuthConfig(cluster.albAuthConfig) ? cluster.albAuthConfig : null;

  const [editing, setEditing] = React.useState(false);
  // Pool id of the currently-picked pool — drives the dependent app-
  // client query. Derived from the picked pool, or parsed out of an
  // existing/pasted ARN (…:userpool/<poolId>) so the client picker
  // works when editing an already-saved config.
  const [poolId, setPoolId] = React.useState(() => poolIdFromArn(existing?.user_pool_arn ?? ""));

  // Cognito pool list for the picker (#859). Only fetched for AWS
  // clusters (the resolver returns [] otherwise) and only while the
  // edit form is open — no point querying AWS on every settings view.
  const isAws = cluster.providerPluginSlug === "aws";
  const poolsQuery = useQuery<CognitoUserPoolsQuery>(COGNITO_USER_POOLS, {
    variables: { clusterId: cluster.id },
    skip: !editing || !isAws,
    fetchPolicy: "cache-and-network",
  });

  // Dependent app-client list — fetched once a pool is selected.
  const clientsQuery = useQuery<CognitoUserPoolClientsQuery>(COGNITO_USER_POOL_CLIENTS, {
    variables: { clusterId: cluster.id, poolId },
    skip: !editing || !isAws || !poolId,
    fetchPolicy: "cache-and-network",
  });

  const [update, { loading: updating }] = useMutation<{
    updateTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(UPDATE_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [reconcile, { loading: reconciling }] = useMutation<{
    reconcileClusterIngresses: MutationResult<ReconcileClusterIngressesResult>;
  }>(RECONCILE_CLUSTER_INGRESSES);

  /**
   * Persist ``config`` (object = enable, null = disable) then push it
   * onto every live managed-subdomain Ingress. The two mutations run in
   * sequence: the save has to land before the reconcile reads the row.
   * The reconcile envelope stays ``ok`` even on partial failure, so we
   * surface the applied count and fold any per-namespace errors into a
   * follow-up warning toast.
   */
  async function persistAndReconcile(config: IngressAuthConfig | null) {
    const { data: updateData } = await update({
      variables: { input: { id: cluster.id, albAuthConfig: config } },
    });
    if (!updateData?.updateTenantCluster.ok) {
      toast.error(updateData?.updateTenantCluster.errors?.[0]?.message ?? "Save failed.");
      return false;
    }

    const { data: reconcileData } = await reconcile({
      variables: { input: { clusterId: cluster.id } },
    });
    const result = reconcileData?.reconcileClusterIngresses;
    if (!result?.ok) {
      toast.error(result?.errors?.[0]?.message ?? "Reconcile failed.");
      return false;
    }

    const count = result.data?.reconciledCount ?? 0;
    const noun = count === 1 ? "app" : "apps";
    if (config) {
      toast.success(`Auth gate applied to ${count} ${noun}.`);
    } else {
      toast.success(`Auth gate removed from ${count} ${noun}.`);
    }
    const reconcileErrors = result.data?.errors ?? [];
    if (reconcileErrors.length > 0) {
      toast.warning(
        `${reconcileErrors.length} ingress(es) could not be reconciled — check cluster events.`
      );
    }
    return true;
  }

  /** Open the edit form, seeded from the saved config. */
  function onOpenForm() {
    setPoolId(poolIdFromArn(existing?.user_pool_arn ?? ""));
    setEditing(true);
  }

  // Flip off: clear config + strip annotations from live Ingresses.
  async function onDisable() {
    await persistAndReconcile(null);
    setEditing(false);
  }

  // Re-apply the (already saved) config onto the cluster's Ingresses.
  async function onApply() {
    await persistAndReconcile(existing);
  }

  // Save the form's config then apply it in one go.
  async function onSaveAndApply(config: IngressAuthConfig | null) {
    if (config === null) {
      toast.error("All three fields are required to enable the auth gate.");
      return;
    }
    const ok = await persistAndReconcile(config);
    if (ok) setEditing(false);
  }

  return {
    providerPluginSlug: cluster.providerPluginSlug,
    ingressClass: cluster.ingressClass,
    existing,
    isAws,
    editing,
    poolId,
    onPoolIdChange: setPoolId,
    pools: poolsQuery.data?.astroliftCognitoUserPools ?? [],
    poolsLoading: poolsQuery.loading,
    // Surface the live-query failure so the operator knows to fall back to
    // paste mode rather than staring at an empty dropdown.
    poolsErrored: !!poolsQuery.error,
    clients: clientsQuery.data?.astroliftCognitoUserPoolClients ?? [],
    clientsLoading: clientsQuery.loading,
    busy: updating || reconciling,
    reconciling,
    onOpenForm,
    onCancelEdit: () => setEditing(false),
    onDisable,
    onApply,
    onSaveAndApply,
  };
}
