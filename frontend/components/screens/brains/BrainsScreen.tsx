"use client";

import { AppWindowIcon, BrainCircuitIcon, PlugIcon, PlusIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import type * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";
import { cn } from "@/lib/utils";

import type { BrainStatusPart } from "./BrainStatusCell";
import { ConnectBrainDialog } from "./ConnectBrainDialog";
import { brainHref } from "./detail/brain-tabs-model";
import { HostBrainAppDialog } from "./HostBrainAppDialog";
import type { BrainRow, BrainsState } from "./use-brains";

export type BrainsScreenProps = BrainsState & {
  /**
   * One status column of a row: Health, Last rebuild or Freshness. The route
   * gives each visible row its own status read (BrainRowStatus); stories and
   * tests pass the pure cell.
   */
  statusCell: (row: BrainRow, part: BrainStatusPart) => React.ReactNode;
};

// The row's link is an ::after overlay across the row; a control in a cell
// has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Apps › Semantic layers (#2320, #2427): the layers the viewer can read,
 * with their team, health, last rebuild, freshness and shares. A row opens
 * the layer. Connect issues the viewer a short-lived read token and shows it
 * once as an MCP client config; Host an app creates an app on the layer from
 * the brain-app template (#2243). Pure view; data from useBrains.
 */
export function BrainsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canCreate,
  connection,
  connecting,
  onConnect,
  onDismissConnection,
  hostingSlug,
  hostProjects,
  hostSubmitting,
  temporary,
  onHost,
  onDismissHost,
  onSubmitHost,
  statusCell,
}: BrainsScreenProps) {
  const t = useTranslations("brains");
  const columns: Column<BrainRow>[] = [
    {
      id: "name",
      header: t("columnName"),
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (b) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={b.name}>
            {b.name}
          </span>{" "}
          <span className="text-muted-foreground block truncate font-mono text-xs" title={b.slug}>
            {b.slug}
          </span>
        </span>
      ),
    },
    {
      id: "owner",
      header: t("columnOwner"),
      sortKey: "team",
      cellClassName: "max-w-56",
      cell: (b) => (
        <span className="block truncate font-mono text-xs" title={`${b.team} / ${b.project}`}>
          {b.team || t("none")} / {b.project || t("none")}
        </span>
      ),
    },
    {
      id: "health",
      header: t("columnHealth"),
      cell: (b) => statusCell(b, "health"),
    },
    {
      id: "rebuilt",
      header: t("columnRebuilt"),
      cell: (b) => statusCell(b, "rebuilt"),
    },
    {
      id: "freshness",
      header: t("columnFreshness"),
      cellClassName: "max-w-56",
      cell: (b) => statusCell(b, "freshness"),
    },
    {
      id: "shared",
      header: t("columnShared"),
      cellClassName: "max-w-56",
      cell: (b) =>
        b.sharedWith.length ? (
          <span className="flex flex-wrap gap-1">
            {b.sharedWith.map((s) => (
              <Badge
                key={s.team}
                variant="secondary"
                className={cn(ABOVE_ROW_LINK, "font-mono")}
                title={t("shareTitle", { team: s.team, access: s.access })}
              >
                {s.team}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{t("notShared")}</span>
        ),
    },
    {
      id: "connect",
      header: t("columnConnect"),
      align: "right",
      cellClassName: ABOVE_ROW_LINK,
      cell: (b) => (
        <span className="inline-flex gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={connecting || !b.environments.length}
            title={b.environments.length ? undefined : t("notServed")}
            onClick={() => void onConnect(b.slug)}
          >
            <PlugIcon className="size-3.5" />
            {t("connect")}
          </Button>
          {canCreate && (
            <Button
              variant="ghost"
              size="sm"
              disabled={!b.environments.length}
              title={b.environments.length ? t("hostTitle") : t("notServed")}
              onClick={() => onHost(b.slug)}
            >
              <AppWindowIcon className="size-3.5" />
              {t("host")}
            </Button>
          )}
        </span>
      ),
    },
  ];
  return (
    <>
      <ListPage<BrainRow>
        header={{
          crumbs: [areaSwitcher(NAV, "apps", "brains"), { label: t("title") }],
          title: t("title"),
          context: t("context"),
          primaryAction: canCreate ? (
            <Button asChild size="sm">
              <Link href="/brains/new">
                <PlusIcon className="size-4" />
                {t("new")}
              </Link>
            </Button>
          ) : undefined,
        }}
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(b) => b.id}
        rowHref={(b) => brainHref(b.slug)}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <BrainCircuitIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          ...(canCreate ? { actionHref: "/brains/new", actionLabel: t("new") } : {}),
        }}
        totalCount={totalCount}
      />
      <ConnectBrainDialog connection={connection} onDismiss={onDismissConnection} />
      <HostBrainAppDialog
        brainSlug={hostingSlug}
        projects={hostProjects}
        submitting={hostSubmitting}
        temporary={temporary}
        onSubmit={(values) => void onSubmitHost(values)}
        onDismiss={onDismissHost}
      />
    </>
  );
}
