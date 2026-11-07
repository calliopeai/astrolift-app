"use client";
import { BoxesIcon, RefreshCwIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { nvkeInventoryHref } from "@/lib/nvke/inventory";
import type { NvkeCollectionView, NvkeResourcesScreenProps } from "./types";

export function NvkeResourcesScreen({
  site,
  collections,
  loading,
  error,
  onRefresh,
}: NvkeResourcesScreenProps) {
  const t = useTranslations("nvke");
  const list = useCursorTableList(collections, {
    id: "nvke.collections",
    label: t("resourcesTitle"),
    searchPlaceholder: "",
    searchable: false,
  });
  const columns: Column<NvkeCollectionView>[] = [
    {
      id: "kind",
      header: t("resourcesTitle"),
      cellClassName: "max-w-80",
      cell: (row) => <span className="block truncate font-medium">{row.resourceKind}</span>,
    },
    {
      id: "surface",
      header: t("interfaces"),
      cell: (row) => <Badge variant="outline">{row.surface}</Badge>,
    },
    {
      id: "action",
      header: t("action"),
      cellClassName: "max-w-80",
      cell: (row) => <code className="block truncate">{row.key}</code>,
    },
    { id: "permission", header: t("permission"), cell: (row) => <code>{row.permission}</code> },
  ];
  if (!site)
    return (
      <PageShell title={t("resourcesTitle")}>
        {loading ? (
          <Skeleton className="h-64" />
        ) : (
          <p role="alert">{error ?? t("siteUnavailable")}</p>
        )}
        <Button variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
      </PageShell>
    );
  return (
    <div className="flex min-w-0 flex-col gap-6 p-6">
      <ListPage
        header={{
          title: t("resourcesTitle"),
          crumbs: [{ label: site.name, href: `/nvke/${site.id}` }],
          context: t("resourcesHelp"),
          primaryAction: (
            <Button variant="outline" onClick={onRefresh}>
              <RefreshCwIcon />
              {t("refresh")}
            </Button>
          ),
        }}
        list={list}
        label={t("resourcesTitle")}
        rows={collections.rows}
        columns={columns}
        getRowId={(row) => row.key}
        rowHref={(row) => nvkeInventoryHref(site.id, row.key)}
        bounded
        loading={loading}
        error={error ? { message: error } : null}
        onRetry={onRefresh}
        empty={{
          icon: <BoxesIcon />,
          title: t("collectionUnavailable"),
          description: t("resourcesHelp"),
        }}
      />
    </div>
  );
}
