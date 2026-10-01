"use client";

import Link from "next/link";
import { BrainCircuitIcon, RocketIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import { SHARED_MODEL_STATUSES } from "./shared-models-list";

export type SharedModelListRow = {
  id: string;
  name: string;
  modelRepo: string;
  revisionSha: string | null;
  clusterId: string;
  clusterName: string;
  clusterSlug: string;
  providerId: string;
  computeMode: string | null;
  status: string;
  reason: string | null;
  ready: boolean | null;
  readinessObservedAt: string | null;
  subscriptionsEnabled: boolean;
};
export type SharedModelsScreenProps = { page: ModelPage<SharedModelListRow> };

export function SharedModelsScreen({ page }: SharedModelsScreenProps) {
  const t = useTranslations("models.shared.deployments");
  const format = useFormatter();
  return (
    <ListPage
      {...page}
      header={{
        crumbs: [{ label: t("title") }],
        title: t("title"),
        context: t("description"),
        primaryAction: (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" asChild>
              <Link href="/models/endpoints">{t("legacy")}</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/models/deploy">
                <RocketIcon className="size-4" />
                {t("browseDeploy")}
              </Link>
            </Button>
          </div>
        ),
      }}
      label={t("label")}
      getRowId={(row) => row.id}
      rowHref={(row) => `/models/shared/${encodeURIComponent(row.id)}`}
      empty={{
        icon: <BrainCircuitIcon />,
        title: t("empty"),
        description: t("emptyDescription"),
        actionHref: "/models/deploy",
        actionLabel: t("browseDeploy"),
      }}
      columns={[
        {
          id: "model",
          header: t("model"),
          cellClassName: "max-w-80",
          cell: (row) => (
            <span className="block min-w-0">
              <span className="block truncate font-medium" title={row.name}>
                {row.name}
              </span>
              <span
                className="text-muted-foreground block truncate font-mono text-xs"
                title={row.modelRepo}
              >
                {row.modelRepo || t("unknown")}
              </span>
              <span
                className="text-muted-foreground block truncate font-mono text-xs"
                title={row.revisionSha ?? undefined}
              >
                {row.revisionSha ?? t("unknownRevision")}
              </span>
            </span>
          ),
        },
        {
          id: "cluster",
          header: t("cluster"),
          cellClassName: "max-w-64",
          cell: (row) => (
            <Button
              type="button"
              variant="ghost"
              className="relative z-10 h-auto max-w-full justify-start text-left break-all whitespace-normal"
              aria-label={t("filterCluster", { cluster: row.clusterName })}
              onClick={() => page.list.setFilter("clusterId", row.clusterId)}
            >
              {row.clusterName} · {row.clusterSlug}
            </Button>
          ),
        },
        {
          id: "compute",
          header: t("compute"),
          cell: (row) =>
            row.computeMode === "cpu" || row.computeMode === "gpu"
              ? t(row.computeMode)
              : t("unknown"),
        },
        {
          id: "status",
          header: t("status"),
          cell: (row) => (
            <span title={row.reason ?? undefined}>
              {(SHARED_MODEL_STATUSES as readonly string[]).includes(row.status)
                ? t(`statuses.${row.status}`)
                : row.status}
            </span>
          ),
        },
        {
          id: "readiness",
          header: t("readiness"),
          cellClassName: "max-w-64",
          cell: (row) => (
            <span className="block space-y-1">
              <span className="block">
                {row.ready === true && row.readinessObservedAt
                  ? t("confirmed")
                  : row.ready === false
                    ? t("unconfirmed")
                    : t("unknown")}
              </span>
              {row.ready === true && row.readinessObservedAt && (
                <span className="text-muted-foreground block text-xs">
                  {t("observedAt", {
                    time: format.dateTime(new Date(row.readinessObservedAt), {
                      dateStyle: "medium",
                      timeStyle: "short",
                      timeZone: "UTC",
                    }),
                  })}
                </span>
              )}
              <span className="text-muted-foreground block text-xs">{t("notLiveHealth")}</span>
            </span>
          ),
        },
        {
          id: "subscriptions",
          header: t("subscriptions"),
          cell: (row) => (row.subscriptionsEnabled ? t("enabled") : t("disabled")),
        },
      ]}
    />
  );
}
