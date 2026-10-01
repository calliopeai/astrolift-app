"use client";

import { AlertTriangleIcon, ServerCrashIcon } from "lucide-react";
import type * as React from "react";
import { useTranslations } from "next-intl";

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
  /** The scoped read failed; last confirmed details may still be visible. */
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
  const t = useTranslations("clusterSettings.source");
  const pending = loading && !cluster;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <ClusterHeader slug={slug} cluster={cluster} loading={pending} active={active} />
      {cluster && error && (
        <div
          role="alert"
          className="border-danger-border bg-danger/5 flex min-w-0 flex-col gap-2 rounded-md border p-4"
        >
          <p className="text-danger-fg text-sm font-medium">{t("readFailed")}</p>
          <p className="text-muted-foreground text-sm">{t("cached")}</p>
          <pre className="font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
            {error}
          </pre>
          {onRetry && (
            <Button
              size="sm"
              variant="outline"
              className="self-start"
              onClick={onRetry}
              disabled={loading}
            >
              {t("retry")}
            </Button>
          )}
        </div>
      )}

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
            <p className="font-medium">{t("readFailed")}</p>
            <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
              {error}
            </p>
          </div>
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          )}
        </div>
      ) : (
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={t("notFound", { slug })}
          description={t("notFoundHelp")}
          actionHref="/clusters"
          actionLabel={t("back")}
        />
      )}
    </div>
  );
}
