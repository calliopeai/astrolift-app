"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_CLUSTERS, LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftProviderPlugin,
  AstroliftTenantCluster,
} from "@/graphql/clusters/clusters.types";
import { useViewToggle } from "@/hooks/use-view-toggle";

interface PluginsResp {
  astroliftProviderPlugins: AstroliftProviderPlugin[];
}
interface ClustersResp {
  astroliftClusters: AstroliftTenantCluster[];
}

/**
 * Provider plugins split into configured (backing at least one of the org's
 * clusters) and available, plus the persisted card/list view. The data half
 * of CloudProvidersPanelView.
 */
export function useCloudProviders() {
  const plugins = useQuery<PluginsResp>(LIST_PROVIDER_PLUGINS, {
    fetchPolicy: "cache-and-network",
  });
  const clusters = useQuery<ClustersResp>(LIST_CLUSTERS, { fetchPolicy: "cache-and-network" });
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_providers", "card");

  const allPlugins = plugins.data?.astroliftProviderPlugins ?? [];
  const clusterList = clusters.data?.astroliftClusters ?? [];

  // slug -> number of clusters backed by that provider. Membership in
  // this map is the "configured for this org" signal.
  const clustersBySlug = new Map<string, number>();
  for (const c of clusterList) {
    if (!c.providerPluginSlug) continue;
    clustersBySlug.set(c.providerPluginSlug, (clustersBySlug.get(c.providerPluginSlug) ?? 0) + 1);
  }

  return {
    loading: (plugins.loading && !plugins.data) || (clusters.loading && !clusters.data),
    error: (plugins.data ? null : plugins.error) ?? (clusters.data ? null : clusters.error) ?? null,
    onRetry: () => {
      void Promise.allSettled([plugins.refetch(), clusters.refetch()]);
    },
    pluginCount: allPlugins.length,
    configured: allPlugins
      .filter((p) => clustersBySlug.has(p.slug))
      .map((plugin) => ({ plugin, clusters: clustersBySlug.get(plugin.slug) ?? 0 })),
    available: allPlugins.filter((p) => !clustersBySlug.has(p.slug)),
    viewMode,
    setViewMode,
  };
}
