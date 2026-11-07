"use client";
import { DownloadIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";
import { nvkeInventoryHref } from "@/lib/nvke/inventory";
import type { NvkeResourceScreenProps } from "./types";
import { NvkeResourceActionPanel } from "./NvkeResourceActionPanel";

export function NvkeResourceScreen({
  siteId,
  operationId,
  siteName,
  collection,
  item,
  observedAt,
  loading,
  pending,
  error,
  canRead,
  onRefresh,
  onDownload,
  resourceActions,
}: NvkeResourceScreenProps) {
  const t = useTranslations("nvke");
  const [confirm, setConfirm] = useState(false);
  if (!item || !collection)
    return (
      <PageShell title={t("resourcesTitle")}>
        {loading ? (
          <Skeleton className="h-64" />
        ) : (
          <p role="alert">{error ?? t("resourceUnavailable")}</p>
        )}
        <Button variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
      </PageShell>
    );
  return (
    <PageShell
      title={item.name || item.identity}
      description={
        <Link href={nvkeInventoryHref(siteId, collection.key, operationId)}>
          {siteName} · {collection.resourceKind}
        </Link>
      }
      actions={
        <>
          <Button variant="outline" onClick={onRefresh} disabled={pending}>
            <RefreshCwIcon />
            {t("refresh")}
          </Button>
          <Button
            variant="outline"
            disabled={!canRead || pending || Boolean(error)}
            onClick={() => setConfirm(true)}
          >
            <DownloadIcon />
            {t("nativeDocument")}
          </Button>
        </>
      }
    >
      {error && (
        <p role="alert" className="text-danger-fg text-sm">
          {error}
        </p>
      )}
      <p role="note" className="text-muted-foreground text-sm">
        {t("historicalHelp")}
      </p>
      <DefinitionList
        items={[
          { term: t("observedAt"), description: observedAt ?? "—" },
          { term: t("identity"), description: item.identity },
          { term: t("namespace"), description: item.namespace || "—" },
          { term: t("resourceUid"), description: item.uid || "—" },
          { term: t("resourceVersion"), description: item.resourceVersion || "—" },
          { term: t("version"), description: item.kubernetesVersion || "—" },
          { term: t("desiredCount"), description: item.desiredCount || "—" },
          {
            term: t("conditions"),
            description: item.conditions.map((c) => `${c.name}: ${c.status}`).join("; ") || "—",
          },
        ]}
      />
      {item.conditionsOmitted > 0 && (
        <p className="text-muted-foreground text-sm">
          {t("conditionsOmitted", { count: item.conditionsOmitted })}
        </p>
      )}
      {resourceActions && <NvkeResourceActionPanel {...resourceActions} />}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("nativeDocument")}
        description={t("downloadHelp")}
        confirmLabel={t("nativeDocument")}
        confirmDisabled={!canRead || pending || Boolean(error)}
        onConfirm={async () => {
          await onDownload();
        }}
      />
    </PageShell>
  );
}
