"use client";

import { BrainCircuitIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import type { BrainRow, BrainsState } from "./use-brains";

export type BrainsScreenProps = BrainsState;

// The row's link is an ::after overlay across the row; a link in a cell has
// to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Apps › Semantic layers (#2320): the hosted brain apps the viewer can read,
 * with where they are served and which teams they are shared with. Sharing a
 * layer is sharing its app (Access on the app). Pure view; data from
 * useBrains.
 */
export function BrainsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: BrainsScreenProps) {
  const t = useTranslations("brains");
  const columns: Column<BrainRow>[] = [
    {
      id: "name",
      header: t("columnName"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (b) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={b.name}>
            {b.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs" title={b.slug}>
            {b.slug}
          </span>
        </span>
      ),
    },
    {
      id: "owner",
      header: t("columnOwner"),
      cellClassName: "max-w-64",
      cell: (b) => (
        <span className="block truncate text-xs" title={`${b.team} / ${b.project}`}>
          {b.team || t("none")} / {b.project || t("none")}
        </span>
      ),
    },
    {
      id: "endpoints",
      header: t("columnEndpoints"),
      cellClassName: `${ABOVE_ROW_LINK} max-w-80`,
      cell: (b) =>
        b.environments.length ? (
          <span className="block min-w-0 space-y-0.5">
            {b.environments.map((env) => (
              <a
                key={env.name}
                href={env.url}
                target="_blank"
                rel="noreferrer"
                className="block truncate font-mono text-xs hover:underline"
                title={`${env.name}: ${env.url}`}
              >
                {env.name}: {env.url}
              </a>
            ))}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{t("notServed")}</span>
        ),
    },
    {
      id: "shared",
      header: t("columnShared"),
      cell: (b) =>
        b.sharedWith.length ? (
          <span className="flex flex-wrap gap-1">
            {b.sharedWith.map((s) => (
              <Badge
                key={s.team}
                variant="secondary"
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
      id: "app",
      header: t("columnApp"),
      cellClassName: ABOVE_ROW_LINK,
      cell: (b) => (
        <Link href={`/apps/${b.slug}/access`} className="text-xs hover:underline">
          {t("manageAccess")}
        </Link>
      ),
    },
  ];
  return (
    <ListPage<BrainRow>
      header={{
        crumbs: [areaSwitcher(NAV, "apps", "brains"), { label: t("title") }],
        title: t("title"),
        context: t("context"),
      }}
      list={list}
      label={t("title")}
      columns={columns}
      rows={rows}
      getRowId={(b) => b.id}
      rowHref={(b) => `/apps/${b.slug}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BrainCircuitIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      totalCount={totalCount}
    />
  );
}
