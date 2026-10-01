"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { toast } from "sonner";

import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  LIST_CLUSTERS,
  REFRESH_CLUSTER_MANAGEMENT,
  UNREGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

// The Clusters list reads `ListClustersPage` and the detail header
// `GetCluster`, while LIST_CLUSTERS still backs /ops, /providers,
// /administration/metrics and the fleet map. All of them have to be
// refreshed after a lifecycle change or one goes stale; the page and the
// detail by operation name, since their variables carry the list state
// and the slug.
const REFETCH_LIST = [{ query: LIST_CLUSTERS }, "ListClustersPage", "GetCluster"];

export interface ClusterActions {
  deleting: boolean;
  bringing: boolean;
  refreshing: boolean;
  /** Throws on failure: ConfirmDialog keeps itself open and shows the error. */
  onUnregister: (c: AstroliftTenantCluster) => Promise<void>;
  onBring: (c: AstroliftTenantCluster) => Promise<void>;
  onRefresh: (c: AstroliftTenantCluster, forcePreflight?: boolean) => Promise<void>;
}

/** The cluster lifecycle mutations, shared by the list rows and the detail header. */
export function useClusterActions(): ClusterActions {
  const t = useTranslations("clusters.unregister");
  const [unregister, { loading: deleting }] = useMutation<{
    unregisterTenantCluster: MutationResult<{ id: string; deleted: boolean }>;
  }>(UNREGISTER_TENANT_CLUSTER, {
    refetchQueries: (result) => (result.data?.unregisterTenantCluster.ok ? REFETCH_LIST : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });
  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });

  async function onUnregister(c: AstroliftTenantCluster) {
    const { data } = await unregister({ variables: { input: { id: c.id } } });
    if (data?.unregisterTenantCluster.ok) {
      toast.success(t("accepted", { slug: c.slug }));
    } else {
      throw new Error(data?.unregisterTenantCluster.errors?.[0]?.message ?? t("failed"));
    }
  }

  async function onBring(c: AstroliftTenantCluster) {
    const { data } = await bring({ variables: { input: { clusterId: c.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${c.slug} into management — this can take up to a minute.`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function onRefresh(c: AstroliftTenantCluster, forcePreflight = false) {
    const { data } = await refresh({
      variables: { input: { clusterId: c.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight ? `Refreshing ${c.slug} (full preflight)` : `Refreshing ${c.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  return { deleting, bringing, refreshing, onUnregister, onBring, onRefresh };
}
