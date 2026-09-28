"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  LIST_CLUSTERS,
  LIST_PROVIDER_PLUGINS,
  PROVIDER_REGIONS,
  REGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftProviderPlugin,
  AstroliftTenantCluster,
} from "@/graphql/clusters/clusters.types";
import type { ProviderRegionsQuery } from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";

export type ProviderRegion = ProviderRegionsQuery["astroliftProviderRegions"][number];

export interface RegisterClusterInput {
  name: string;
  slug: string;
  providerPluginSlug: string;
  authMethod: string;
  region: string | null;
  endpoint: string | null;
  ingressClass: string;
  authConfig: unknown;
}

/**
 * The provider catalog, the selected provider's regions and the register
 * mutation behind the register sheet. The data half of
 * RegisterClusterSheet. The selected plugin lives here, not in the view,
 * because it keys the regions query.
 */
export function useRegisterCluster() {
  const plugins = useQuery<{
    astroliftProviderPlugins: AstroliftProviderPlugin[];
  }>(LIST_PROVIDER_PLUGINS);
  const pluginList = plugins.data?.astroliftProviderPlugins ?? [];

  const [pluginSlug, setPluginSlug] = React.useState("");

  React.useEffect(() => {
    const list = plugins.data?.astroliftProviderPlugins ?? [];
    if (!pluginSlug && list.length > 0) {
      setPluginSlug(list[0].slug);
    }
  }, [plugins.data, pluginSlug]);

  // Region picker (#860). k8s_native has no region concept, so the query
  // is skipped (the backend resolver returns [] for it anyway).
  const regions = useQuery<ProviderRegionsQuery>(PROVIDER_REGIONS, {
    variables: { providerPluginSlug: pluginSlug },
    skip: !pluginSlug || pluginSlug === "k8s_native",
    // The list rarely changes within a session; cache-first avoids a
    // refetch every time the operator re-opens the sheet.
    fetchPolicy: "cache-first",
  });

  const [register, { loading: registering }] = useMutation<{
    registerTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(REGISTER_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the cluster registered, so the view can close. */
  async function onRegister(input: RegisterClusterInput): Promise<boolean> {
    const { data } = await register({ variables: { input } });
    if (data?.registerTenantCluster.ok) {
      toast.success(`Registered ${input.slug}`);
      return true;
    }
    toast.error(data?.registerTenantCluster.errors?.[0]?.message ?? "Failed");
    return false;
  }

  return {
    plugins: pluginList,
    pluginSlug,
    onPluginSlugChange: setPluginSlug,
    regions: regions.data?.astroliftProviderRegions ?? [],
    regionsLoading: regions.loading,
    registering,
    onRegister,
  };
}
