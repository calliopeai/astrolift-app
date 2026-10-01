"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { UPDATE_TENANT_CLUSTER } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { type CentralAuthDraft, oidcComplete, oidcView } from "./types";

function useUpdateCluster() {
  return useMutation<{ updateTenantCluster: MutationResult<AstroliftTenantCluster> }>(
    UPDATE_TENANT_CLUSTER,
    { refetchQueries: ["GetCluster"], awaitRefetchQueries: true }
  );
}

/**
 * The cluster's central auth (``oidcAuthConfig``, #2119). Secrets are
 * write-only: the server reports whether each is set, a blank field keeps
 * the stored value, and the save never sends one the operator did not just
 * type. The data half of CentralAuthView.
 */
export function useCentralAuth(cluster: AstroliftTenantCluster) {
  const t = useTranslations("clusterSettings.centralAuth");
  const view = oidcView(cluster);
  const [update, { loading }] = useMutation<{
    updateTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(UPDATE_TENANT_CLUSTER, {
    refetchQueries: (result) => (result.data?.updateTenantCluster.ok ? ["GetCluster"] : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });

  /** Resolves true when saved (the view closes the form). */
  async function onSave(draft: CentralAuthDraft): Promise<boolean> {
    // Start from what the server showed, so keys this form does not edit
    // (logout URL, upstream connector) survive. Secrets are not in the view:
    // the server keeps each one this payload omits.
    const kept = Object.fromEntries(
      Object.entries(view ?? {}).filter(([k, v]) => !k.endsWith("_set") && typeof v === "string")
    ) as Record<string, string>;
    const config: Record<string, string> = {
      ...kept,
      discovery_url: draft.discoveryUrl.trim(),
      client_id: draft.clientId.trim(),
      auth_proxy_host: draft.authHost.trim(),
    };
    if (draft.jwksUri.trim()) config.jwks_uri = draft.jwksUri.trim();
    else delete config.jwks_uri;
    if (draft.clientSecret) config.client_secret = draft.clientSecret;

    try {
      const { data } = await update({
        variables: { input: { id: cluster.id, oidcAuthConfig: config } },
      });
      if (data?.updateTenantCluster.ok) {
        toast.success(t("saved"));
        return true;
      }
      toast.error(data?.updateTenantCluster.errors?.[0]?.message ?? t("saveFailed"));
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("saveFailed"));
    }
    return false;
  }

  return { clusterId: cluster.id, view, saving: loading, onSave };
}

/**
 * The ingress class: the switch that moves apps between edges. The server
 * refuses a flip that would drop the gate (#1616). The data half of
 * IngressClassView.
 */
export function useIngressClass(cluster: AstroliftTenantCluster) {
  const [update] = useUpdateCluster();

  /** Throws on refusal, so the confirm dialog stays open with the error. */
  async function onApply(target: string, syncManifests: boolean) {
    const { data } = await update({
      variables: { input: { id: cluster.id, ingressClass: target, syncManifests } },
    });
    if (!data?.updateTenantCluster.ok) {
      throw new Error(data?.updateTenantCluster.errors?.[0]?.message ?? "Change failed.");
    }
    toast.success(`Ingress class is now ${target}.`);
  }

  return {
    ingressClass: cluster.ingressClass,
    albGate: Boolean(cluster.albAuthConfig),
    centralAuthConfigured: oidcComplete(oidcView(cluster)),
    onApply,
  };
}
