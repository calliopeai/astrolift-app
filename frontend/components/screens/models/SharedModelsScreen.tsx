"use client";

import type { ReactNode } from "react";
import type {
  CommonNativeConnectionFieldsFragment,
  NativeModelSourceFieldsFragment,
} from "@/graphql/__generated__/operations";
import { modelSourceMode, nativeModelFamily, validNativeSource } from "./native-model-source";
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
  nativeSource?: NativeModelSourceFieldsFragment | null;
  nativeConnection?: CommonNativeConnectionFieldsFragment | null;
  localManifestSha256?: string | null;
  desiredResources?: {
    cpuRequest: string | null;
    memoryRequest: string | null;
    gpuCount: number | null;
  };
};
export type SharedModelsScreenProps = {
  page: ModelPage<SharedModelListRow>;
  addChoices?: ReactNode;
};

export function SharedModelsScreen({ page, addChoices }: SharedModelsScreenProps) {
  const t = useTranslations("models.shared.deployments");
  const inventory = useTranslations("models.shared.inventory");
  const connections = useTranslations("models.shared.connections");
  const format = useFormatter();
  const native = useTranslations("models.native.details");
  const family = useTranslations("models.native.common");
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
              <Link href="/models/connections">{connections("viewRequests")}</Link>
            </Button>
            <Button size="sm" variant="outline" asChild>
              <Link href="/models/endpoints">{t("legacy")}</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href={addChoices ? "#model-add" : "/models/deploy"}>
                <RocketIcon className="size-4" />
                {inventory("addModel")}
              </Link>
            </Button>
          </div>
        ),
      }}
      notice={
        addChoices ? (
          <div id="model-add" className="scroll-mt-20">
            {addChoices}
          </div>
        ) : undefined
      }
      label={t("label")}
      getRowId={(row) => row.id}
      rowHref={(row) => `/models/shared/${encodeURIComponent(row.id)}`}
      empty={{
        icon: <BrainCircuitIcon />,
        title: t("empty"),
        description: t("emptyDescription"),
        actionHref: addChoices ? "#model-add" : "/models/deploy",
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
                title={modelSourceMode(row) === "hosted" ? row.modelRepo : undefined}
              >
                {modelSourceMode(row) === "native"
                  ? row.nativeSource?.sourceId
                  : modelSourceMode(row) === "hosted"
                    ? row.modelRepo || t("unknown")
                    : native("unavailable")}
              </span>
              <span
                className="text-muted-foreground block truncate font-mono text-xs"
                title={
                  (modelSourceMode(row) === "native" && validNativeSource(row.nativeSource)
                    ? row.nativeSource.sourceFingerprint
                    : modelSourceMode(row) === "hosted" && row.sourceKind === "local_artifact"
                      ? row.localManifestSha256
                      : modelSourceMode(row) === "hosted"
                        ? row.revisionSha
                        : null) ?? undefined
                }
              >
                {(modelSourceMode(row) === "native_unavailable"
                  ? native("unavailable")
                  : modelSourceMode(row) === "native" && validNativeSource(row.nativeSource)
                    ? row.nativeSource.sourceFingerprint
                    : modelSourceMode(row) === "hosted" && row.sourceKind === "local_artifact"
                      ? row.localManifestSha256
                      : modelSourceMode(row) === "hosted"
                        ? row.revisionSha
                        : null) ?? t("unknownRevision")}
              </span>
            </span>
          ),
        },
        {
          id: "source",
          header: inventory("source"),
          cell: (row) =>
            modelSourceMode(row) === "native" && validNativeSource(row.nativeSource)
              ? "Amazon Bedrock"
              : modelSourceMode(row) === "hosted" && row.sourceKind === "local_artifact"
                ? inventory("local")
                : modelSourceMode(row) === "hosted" && row.sourceKind === "huggingface"
                  ? inventory("huggingface")
                  : modelSourceMode(row) === "native_unavailable"
                    ? `${family(nativeModelFamily(row) ?? "UNKNOWN")} · ${native("unavailable")}`
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
            modelSourceMode(row) !== "hosted"
              ? native("notApplicable")
              : inventory("resourceSummary", {
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
            modelSourceMode(row) !== "hosted"
              ? native("notApplicable")
              : row.computeMode === "cpu" || row.computeMode === "gpu"
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
          cell: (row) =>
            modelSourceMode(row) !== "hosted" ? (
              <span>
                {modelSourceMode(row) === "native" && validNativeSource(row.nativeSource)
                  ? native("description")
                  : modelSourceMode(row) === "native_unavailable"
                    ? native("unavailable")
                    : native("unsupported")}
              </span>
            ) : (
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
