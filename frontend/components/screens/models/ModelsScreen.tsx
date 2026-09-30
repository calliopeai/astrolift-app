"use client";

import { BrainCircuitIcon, RocketIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import {
  computeModeOf,
  isHosted,
  type ModelEndpoint,
  modelId,
  modelStatusDot,
  ownerHref,
  ownerLabel,
} from "./models-list";
import { replicasOf } from "./use-model-replicas";
import type { ModelsState } from "./use-models";

export type { ModelEndpoint } from "./models-list";

export type ModelsScreenProps = ModelsState;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

function modelColumns(
  t: ReturnType<typeof useTranslations<"models.shared.deployments">>
): Column<ModelEndpoint>[] {
  return [
    {
      id: "model",
      header: t("model"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (m) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={m.name}>
            {m.name}
          </span>
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={modelId(m)}
          >
            {modelId(m) || t("noModelId")}
          </span>
        </span>
      ),
    },
    {
      id: "serving",
      header: t("serving"),
      sortKey: "variant",
      cell: (m) => <Badge variant={isHosted(m) ? "default" : "secondary"}>{m.variant}</Badge>,
    },
    {
      id: "hardware",
      header: t("compute"),
      cell: (m) => (
        <span className="text-muted-foreground font-mono text-xs">{t(computeModeOf(m))}</span>
      ),
    },
    {
      id: "owner",
      header: t("owner"),
      cellClassName: `${ABOVE_ROW_LINK} max-w-64`,
      cell: (m) => (
        <Link
          className="block truncate font-mono text-xs hover:underline"
          href={ownerHref(m)}
          title={ownerLabel(m)}
        >
          {ownerLabel(m)}
        </Link>
      ),
    },
    {
      id: "status",
      header: t("status"),
      sortKey: "status",
      cell: (m) => (
        <span
          className="inline-flex min-w-0 items-center gap-1.5 text-sm"
          title={m.statusError || undefined}
        >
          <StatusDot status={modelStatusDot(m.status)} />
          <span className="truncate">{m.status}</span>
        </span>
      ),
    },
    {
      id: "replicas",
      header: t("desiredReplicas"),
      align: "right",
      cell: (m) =>
        m.variant === "vllm" ? (
          <span className="font-mono text-xs">
            {replicasOf((m.config ?? {}) as Record<string, unknown>)}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{t("providerManaged")}</span>
        ),
    },
  ];
}

/**
 * Existing app/project endpoints remain a separate, server-paged surface.
 * Variant names do not establish compute mode; that needs an explicit stored
 * fact. Existing ownership links and deployment contracts remain unchanged.
 */
export function ModelsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: ModelsScreenProps) {
  const t = useTranslations("models.shared.deployments");
  const playground = useTranslations("playground");
  return (
    <ListPage<ModelEndpoint>
      header={{
        crumbs: [{ label: t("title"), href: "/models" }, { label: t("legacy") }],
        title: t("legacy"),
        context: t("legacyDescription"),
        primaryAction: (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" asChild>
              <Link href="/models">{t("title")}</Link>
            </Button>
            <Button size="sm" variant="outline" asChild>
              <Link href="/playground">{playground("title")}</Link>
            </Button>
            <Button size="sm" asChild>
              <Link href="/models/deploy/legacy">
                <RocketIcon className="size-4" />
                {t("legacyDeploy")}
              </Link>
            </Button>
          </div>
        ),
      }}
      list={list}
      label={t("legacy")}
      columns={modelColumns(t)}
      rows={rows}
      getRowId={(m) => m.id}
      rowHref={(m) => `/models/${m.id}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BrainCircuitIcon className="size-5" />,
        title: t("legacyEmptyTitle"),
        description: t("legacyEmptyDescription"),
        actionHref: "/models/deploy/legacy",
        actionLabel: t("legacyDeploy"),
      }}
      totalCount={totalCount}
    />
  );
}
