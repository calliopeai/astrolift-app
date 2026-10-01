"use client";

import { usePathname } from "next/navigation";
import type * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { StatusDot } from "@/components/StatusDot";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { type ClusterTabKey, clusterTabs } from "./ClusterTabs";
import { clusterCrumbs, LIFECYCLE_LABEL, type Lifecycle, providerLabel } from "./clusters-list";

export type ClusterHeaderCluster = Pick<
  AstroliftTenantCluster,
  "name" | "slug" | "providerPluginSlug" | "region" | "lifecycle" | "isActive"
>;

export interface ClusterHeaderProps {
  slug: string;
  /** Null while loading, or when no cluster has this slug. */
  cluster: ClusterHeaderCluster | null;
  loading?: boolean;
  /** Current source-state title supplied by a caller with read/error knowledge. */
  emptyTitle?: React.ReactNode;
  /** The tab on screen; read from the pathname when absent. */
  active?: ClusterTabKey;
  /** The lifecycle's one action (Bring into management, Retry, Refresh setup). */
  primaryAction?: React.ReactNode;
  /** The `⋯` menu. */
  menu?: React.ReactNode;
}

const DOT: Record<Lifecycle, "ok" | "warn" | "error" | "muted" | "pending"> = {
  registered: "muted",
  managing: "pending",
  managed: "ok",
  error: "error",
};

/**
 * Every cluster page's header (spec 44 §4.4, §5.2): Admin ▾ › Clusters ›
 * name; the name, its lifecycle, provider and region, the primary action and
 * `⋯`; then the cluster's tabs as the one row. Pure.
 */
export function ClusterHeader({
  slug,
  cluster,
  loading = false,
  emptyTitle,
  active,
  primaryAction,
  menu,
}: ClusterHeaderProps) {
  const pathname = usePathname() ?? "";

  if (!cluster) {
    return (
      <ShellHeader
        crumbs={clusterCrumbs(slug)}
        title={loading ? <Skeleton className="h-6 w-48" /> : (emptyTitle ?? "Cluster not found")}
        tabs={loading ? clusterTabs(slug, pathname, active) : undefined}
        tabsAriaLabel="Cluster tabs"
      />
    );
  }

  const lifecycle = (cluster.lifecycle as Lifecycle) ?? "registered";
  return (
    <ShellHeader
      crumbs={clusterCrumbs(cluster.name)}
      title={<span title={cluster.name}>{cluster.name}</span>}
      status={
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm">
          <StatusDot status={DOT[lifecycle] ?? "muted"} />
          {LIFECYCLE_LABEL[lifecycle] ?? lifecycle}
          {!cluster.isActive && <span className="text-muted-foreground">· inactive</span>}
        </span>
      }
      context={
        <>
          {providerLabel(cluster.providerPluginSlug)}
          {cluster.region && (
            <>
              {" · "}
              <span className="font-mono">{cluster.region}</span>
            </>
          )}
        </>
      }
      primaryAction={primaryAction}
      menu={menu}
      tabs={clusterTabs(cluster.slug, pathname, active)}
      tabsAriaLabel="Cluster tabs"
    />
  );
}
