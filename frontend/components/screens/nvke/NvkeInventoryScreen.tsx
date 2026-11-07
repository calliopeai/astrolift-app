"use client";
import { BoxesIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { nvkeResourceHref } from "@/lib/nvke/inventory";
import { NvkeNativePanel } from "./NvkeNativePanel";
import type { NvkeInventoryItemView, NvkeInventoryScreenProps } from "./types";

export function NvkeInventoryScreen(props: NvkeInventoryScreenProps) {
  const { site, collection, table, operation, observedAt, loading, error, onRefresh } = props;
  const t = useTranslations("nvke");
  const list = useCursorTableList(table, {
    id: "nvke.inventory",
    label: t("resourcesTitle"),
    searchPlaceholder: t("inventorySearch"),
  });
  const columns: Column<NvkeInventoryItemView>[] = [
    {
      id: "name",
      header: t("name"),
      cellClassName: "max-w-80",
      cell: (row) => <span className="block truncate font-medium">{row.name || row.identity}</span>,
    },
    {
      id: "identity",
      header: t("identity"),
      cellClassName: "max-w-72",
      cell: (row) => <code className="block truncate">{row.identity}</code>,
    },
    {
      id: "version",
      header: t("version"),
      cellClassName: "max-w-44",
      cell: (row) => (
        <code className="block truncate">
          {row.kubernetesVersion || row.resourceVersion || "—"}
        </code>
      ),
    },
    { id: "count", header: t("desiredCount"), cell: (row) => row.desiredCount || "—" },
  ];
  if (!site || !collection)
    return (
      <PageShell title={t("resourcesTitle")}>
        {loading ? (
          <Skeleton className="h-64" />
        ) : (
          <p role="alert">{error ?? t("collectionUnavailable")}</p>
        )}
        <Button variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
      </PageShell>
    );
  const complete = operation?.isInventory && operation.status === "SUCCEEDED";
  return (
    <PageShell
      title={collection.resourceKind}
      description={
        <Link href={`/nvke/${site.id}/resources`}>
          {site.name} · {t("resourcesTitle")}
        </Link>
      }
      actions={
        <Button variant="outline" onClick={onRefresh}>
          <RefreshCwIcon />
          {t("refresh")}
        </Button>
      }
    >
      {error && (
        <p role="alert" className="text-danger-fg text-sm">
          {error}
        </p>
      )}
      <p className="text-muted-foreground text-sm">{t("inventoryHelp")}</p>
      {operation && (
        <div className="flex min-w-0 flex-wrap items-center gap-3 text-sm">
          <Badge variant="outline">{operation.status}</Badge>
          <Link className="hover:underline" href={`/nvke/${site.id}/operations/${operation.id}`}>
            {t("operations")}
          </Link>
          {observedAt && (
            <span>
              {t("observedAt")}: <time dateTime={observedAt}>{observedAt}</time>
            </span>
          )}
        </div>
      )}
      {complete ? (
        <>
          <p role="note" className="text-muted-foreground text-sm">
            {t("historicalHelp")}
          </p>
          <ListPage
            embedded
            list={list}
            label={t("resourcesTitle")}
            rows={table.rows}
            columns={columns}
            getRowId={(row) => row.key}
            rowHref={(row) => nvkeResourceHref(site.id, collection.key, operation.id, row.key)}
            loading={table.state === "loading"}
            stale={table.isStale}
            error={table.state === "error" ? { message: t("readFailed") } : null}
            onRetry={table.retry}
            nextCursor={table.nextCursor}
            totalCount={table.totalCount}
            empty={{
              icon: <BoxesIcon />,
              title: t("emptyInventory"),
              description: t("inventoryHelp"),
            }}
          />
        </>
      ) : (
        <p role="status" className="text-muted-foreground text-sm">
          {operation ? t("acceptedHelp") : t("noInventory")}
        </p>
      )}
      <NvkeNativePanel {...props} />
    </PageShell>
  );
}
