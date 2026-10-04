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
  sharingMode?: "SHARED" | "DEDICATED";
  dedicatedAppId?: string | null;
  dedicatedAppName?: string | null;
  dedicatedAppSlug?: string | null;
  sourceKind?: string;
  localManifestSha256?: string | null;
  desiredResources?: {
    cpuRequest: string | null;
    memoryRequest: string | null;
    gpuCount: number | null;
  };
};
export type SharedModelsScreenProps = { page: ModelPage<SharedModelListRow> };

export function SharedModelsScreen({ page }: SharedModelsScreenProps) {
  const t = useTranslations("models.shared.deployments");
  const inventory = useTranslations("models.shared.inventory");
  const format = useFormatter();
  return (
    <ListPage
      {...page}
      header={{
        crumbs: [{ label: inventory("title") }],
        title: inventory("title"),
        context: inventory("description"),
        primaryAction: (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" asChild>
              <Link href="/models/endpoints">{t("legacy")}</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/models/deploy">
                <RocketIcon className="size-4" />
                {inventory("addModel")}
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
        actionLabel: inventory("addModel"),
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
                title={
                  (row.sourceKind === "local_artifact"
                    ? row.localManifestSha256
                    : row.revisionSha) ?? undefined
                }
              >
                {(row.sourceKind === "local_artifact"
                  ? row.localManifestSha256
                  : row.revisionSha) ?? t("unknownRevision")}
              </span>
            </span>
          ),
        },
        {
          id: "source",
          header: inventory("source"),
          cell: (row) =>
            row.sourceKind === "local_artifact"
              ? inventory("local")
              : row.sourceKind === "huggingface"
                ? inventory("huggingface")
                : inventory("unknownSource"),
        },
        {
          id: "access",
          header: inventory("access"),
          cell: (row) =>
            row.sharingMode === "SHARED" ? (
              inventory("shared")
            ) : row.sharingMode === "DEDICATED" ? (
              <span>
                {inventory("dedicated")}
                <span className="text-muted-foreground block">
                  {row.dedicatedAppName
                    ? inventory("dedicatedApp", { app: row.dedicatedAppName })
                    : inventory("unknownAccess")}
                </span>
              </span>
            ) : (
              inventory("unknownAccess")
            ),
        },
        {
          id: "resources",
          header: inventory("resources"),
          cell: (row) =>
            inventory("resourceSummary", {
              cpu: row.desiredResources?.cpuRequest ?? t("unknown"),
              memory: row.desiredResources?.memoryRequest ?? t("unknown"),
              gpu: row.desiredResources?.gpuCount ?? t("unknown"),
            }),
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
