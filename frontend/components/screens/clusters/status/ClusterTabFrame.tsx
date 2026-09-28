"use client";

import { AlertTriangleIcon, ServerCrashIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ClusterHeader } from "@/components/screens/clusters/list/ClusterHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import type { ClusterSummary } from "./types";

export type ClusterStatusTabKey = "status" | "health" | "activity";

export interface ClusterTabFrameProps {
  slug: string;
  cluster: ClusterSummary | null;
  loading: boolean;
  /** The cluster list query failed and no cluster is cached. */
  error?: string | null;
  onRetry?: () => void;
  /** The active entry in the cluster's tab row. */
  active: ClusterStatusTabKey;
  /** The tab body; rendered only once the cluster is found. */
  children: React.ReactNode;
}

/**
 * The page around the cluster Status, Health and Activity tabs (spec 44
 * §4.4, §5.2): the same ClusterHeader as the overview (Admin › Clusters ›
 * name, the lifecycle and provider, the cluster's one tab row) over the tab
 * body. While the cluster list loads the body is a skeleton of the panel
 * grid; a failed load shows the error with a retry; an unknown slug shows
 * the not-found state.
 */
export function ClusterTabFrame({
  slug,
  cluster,
  loading,
  error,
  onRetry,
  active,
  children,
}: ClusterTabFrameProps) {
  const pending = loading && !cluster;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ClusterHeader slug={slug} cluster={cluster} loading={pending} active={active} />

      {pending ? (
        <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
          <Skeleton className="col-span-12 h-32 w-full" />
          <Skeleton className="col-span-12 h-48 w-full xl:col-span-6" />
          <Skeleton className="col-span-12 h-48 w-full xl:col-span-6" />
        </div>
      ) : cluster ? (
        children
      ) : error ? (
        <div
          role="alert"
          className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
        >
          <ServerCrashIcon className="text-danger size-5" aria-hidden />
          <div className="min-w-0 px-6">
            <p className="font-medium">Could not load this cluster</p>
            <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
              {error}
            </p>
          </div>
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          )}
        </div>
      ) : (
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          description="The cluster doesn't exist or you don't have permission to view it."
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      )}
    </div>
  );
}
