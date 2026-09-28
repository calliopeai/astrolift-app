"use client";

import { AlertTriangleIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import type { ClusterSummary } from "./types";

export interface ClusterTabFrameProps {
  slug: string;
  cluster: ClusterSummary | null;
  loading: boolean;
  /** Page title while the cluster list loads, e.g. "Cluster status". */
  loadingTitle: string;
  /** Suffix after the cluster name, e.g. "Status". */
  tabLabel: string;
  /** The cluster tab row. */
  tabs: React.ReactNode;
  /** The tab body; rendered only once the cluster is found. */
  children: React.ReactNode;
}

/**
 * The page around every cluster detail tab: a loading shell, a not-found
 * state, or the cluster's title + slug + provider badge, the tab row and
 * the tab body.
 */
export function ClusterTabFrame({
  slug,
  cluster,
  loading,
  loadingTitle,
  tabLabel,
  tabs,
  children,
}: ClusterTabFrameProps) {
  if (loading && !cluster) {
    return (
      <PageShell title={loadingTitle} description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!cluster) {
    return (
      <PageShell
        title="Cluster not found"
        description="The cluster doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${cluster.name} · ${tabLabel}`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      {tabs}
      {children}
    </PageShell>
  );
}
