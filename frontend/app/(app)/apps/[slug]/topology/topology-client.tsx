"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, BoxIcon, NetworkIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { AppTopologyMap, appTopology } from "@/components/topology";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface ManagedServicesResp {
  astroliftManagedServices: Array<{
    id: string;
    name: string;
    kind: string;
    variant: string | null;
    status: string;
    environmentName: string;
  }>;
}

/**
 * App > Topology tab (#705). Promotes the topology graph from a small
 * Overview thumbnail to a first-class screen — full-page rendering at
 * a fixed-tall height, room for legend + future drill-in. The Overview
 * keeps a compact thumbnail with "Open Topology" so an operator
 * scanning the landing page sees the graph at a glance and clicks
 * here for the full view.
 */
export function TopologyClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.topology");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });
  // #727 — fetch managed-service bindings so the topology renders
  // RDS / S3 / SES / etc nodes hanging off the workload layer.
  const managedServices = useQuery<ManagedServicesResp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug: slug },
  });

  if (app.loading && !app.data) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  const a = app.data?.astroliftApp;
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

  const wlList = workloads.data?.astroliftWorkloads ?? [];
  const msList = managedServices.data?.astroliftManagedServices ?? [];
  const { nodes, edges } = appTopology(a, wlList, msList);

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="topology" />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <NetworkIcon className="size-4" /> {t("graphTitle")}
          </CardTitle>
          <CardDescription>{t("graphDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {workloads.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-[640px] w-full" />
            </div>
          ) : nodes.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref={appPath(chrome, a.slug, "workloads")}
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
