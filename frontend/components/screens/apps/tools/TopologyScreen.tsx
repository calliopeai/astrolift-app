"use client";

import { AlertTriangleIcon, BoxIcon, NetworkIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { AppTopologyMap } from "@/components/topology";
import type { TopologyEdge, TopologyNode } from "@/components/topology/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

export interface TopologyScreenProps {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "name" | "slug"> | null;
  loading: boolean;
  workloadsLoading: boolean;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  /** Where the empty state's "add a workload" action goes (chrome-aware). */
  workloadsHref: string;
  /** The app tab bar. */
  tabs?: React.ReactNode;
}

/**
 * App > Topology tab (#705). Promotes the topology graph from a small
 * Overview thumbnail to a first-class screen — full-page rendering at
 * a fixed-tall height, room for legend + future drill-in. The Overview
 * keeps a compact thumbnail with "Open Topology" so an operator
 * scanning the landing page sees the graph at a glance and clicks
 * here for the full view.
 */
export function TopologyScreen({
  slug,
  app: a,
  loading,
  workloadsLoading,
  nodes,
  edges,
  workloadsHref,
  tabs,
}: TopologyScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.topology");

  if (loading) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      {tabs}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <NetworkIcon className="size-4" /> {t("graphTitle")}
          </CardTitle>
          <CardDescription>{t("graphDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {workloadsLoading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-[640px] w-full" />
            </div>
          ) : nodes.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref={workloadsHref}
                actionLabel={t("emptyAction")}
              />
            </div>
          ) : (
            <div className="p-4">
              <AppTopologyMap nodes={nodes} edges={edges} height={640} variant="telemetry" />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("legendTitle")}</CardTitle>
          <CardDescription>{t("legendDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="text-muted-foreground grid gap-2 text-xs sm:grid-cols-2">
            <li>
              <span className="text-foreground font-medium">{t("legend.ingress")}</span> —{" "}
              {t("legend.ingressBody")}
            </li>
            <li>
              <span className="text-foreground font-medium">{t("legend.service")}</span> —{" "}
              {t("legend.serviceBody")}
            </li>
            <li>
              <span className="text-foreground font-medium">{t("legend.workload")}</span> —{" "}
              {t("legend.workloadBody")}
            </li>
            <li>
              <span className="text-foreground font-medium">{t("legend.status")}</span> —{" "}
              {t("legend.statusBody")}
            </li>
          </ul>
        </CardContent>
      </Card>
    </PageShell>
  );
}
