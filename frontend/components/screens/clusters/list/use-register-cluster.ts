"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
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

/** Keys of RegisterClusterInput, which are also the mutation's error `field`s. */
export type RegisterField = keyof RegisterClusterInput;

/** A refused register: errors by field, shown in place, and any the form owns. */
export type RegisterResult =
  | { ok: true }
  | { ok: false; fieldErrors: Partial<Record<RegisterField, string>>; formError: string | null };

const FIELDS: RegisterField[] = [
  "name",
  "slug",
  "providerPluginSlug",
  "authMethod",
  "region",
  "endpoint",
  "ingressClass",
  "authConfig",
];

/**
 * The provider catalog, the selected provider's regions and the register
 * mutation behind the register page. The data half of RegisterClusterPage.
 * The selected plugin lives here, not in the view, because it keys the
 * regions query.
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

  const router = useRouter();

  /**
   * On success, a toast (the outcome) and the new cluster's page, where
   * Bring into management is the next step. On refusal, the errors go back
   * to the form to show beside their fields (spec 44 §5.4).
   */
  async function onRegister(input: RegisterClusterInput): Promise<RegisterResult> {
    const { data } = await register({ variables: { input } });
    if (data?.registerTenantCluster.ok) {
      toast.success(`Registered ${input.slug}`);
      router.push(`/clusters/${data.registerTenantCluster.data?.slug ?? input.slug}`);
      return { ok: true };
    }
    const fieldErrors: Partial<Record<RegisterField, string>> = {};
    const rest: string[] = [];
    for (const e of data?.registerTenantCluster.errors ?? []) {
      const field = FIELDS.find((f) => f === e.field);
      if (field && !fieldErrors[field]) fieldErrors[field] = e.message;
      else rest.push(e.message);
    }
    const formError =
      rest.join(" ") || (Object.keys(fieldErrors).length === 0 ? "Registration failed." : null);
    return { ok: false, fieldErrors, formError };
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
