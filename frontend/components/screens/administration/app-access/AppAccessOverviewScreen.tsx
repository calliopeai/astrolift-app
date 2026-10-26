"use client";

import { LockKeyholeIcon, UserIcon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { useFormatters } from "@/lib/i18n/formatters";

import { adminCrumbs } from "../insights/header";
import type { EdgeAccessRow, PersonSummary } from "./edge-access-list";
import { EdgeStatusCell } from "./EdgeStatusCell";
import { PersonLoginsNotice } from "./PersonLoginsNotice";

export interface AppAccessOverviewScreenProps {
  /** List state: views, search, chips, cursor (see edge-access-list.ts). */
  list: ListStateController;
  /** One page: an app's environment per row, ordered by app. */
  rows: EdgeAccessRow[];
  /** Set when the list is for one person (the `person` filter, or Mine). */
  person: PersonSummary | null;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  totalCount: number | null;
}

function Names({ icon, names }: { icon: React.ReactNode; names: string[] }) {
  if (names.length === 0) return null;
  const all = names.join(", ");
  return (
    <span className="flex min-w-0 items-center gap-1 font-mono text-xs" title={all}>
      {icon}
      <span className="truncate">{all}</span>
    </span>
  );
}

/**
 * Admin › App access (#2534): who may enter every app the viewer can read,
 * one row per environment, and whether the edge holds the rule. Filtered by
 * team, project, cluster, edge status, or a person (Mine is the viewer),
 * when it adds how that person gets in. Each row opens the app's own access
 * editor; nothing is changed here. Pure; the data half is useAppAccessOverview.
 */
export function AppAccessOverviewScreen({
  list,
  rows,
  person,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
}: AppAccessOverviewScreenProps) {
  const t = useTranslations("adminAppAccess");
  const fmt = useFormatters();

  const columns: Column<EdgeAccessRow>[] = [
    {
      id: "app",
      header: t("columns.app"),
      cellClassName: "max-w-56",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-sm" title={r.appSlug}>
            {r.appSlug}
          </span>
          <span className="text-muted-foreground truncate font-mono text-xs" title={r.environment}>
            {r.environment}
          </span>
        </div>
      ),
    },
    {
      id: "where",
      header: t("columns.where"),
      cellClassName: "max-w-64",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-xs" title={r.clusterSlug}>
            {r.clusterSlug}
          </span>
          <span
            className="text-muted-foreground truncate font-mono text-xs"
            title={r.hosts.join(", ") || undefined}
          >
            {r.hosts.length ? r.hosts.join(", ") : t("noHosts")}
          </span>
        </div>
      ),
    },
    {
      id: "rule",
      header: t("columns.rule"),
      cellClassName: "max-w-64",
      cell: (r) =>
        r.restricted ? (
          <div className="flex min-w-0 flex-col gap-0.5">
            <Names icon={<UsersIcon className="size-3 shrink-0" />} names={r.groups} />
            <Names icon={<UserIcon className="size-3 shrink-0" />} names={r.users} />
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">{t("open")}</span>
        ),
    },
    ...(person
      ? [
          {
            id: "entry",
            header: t("columns.entry"),
            cellClassName: "max-w-56",
            cell: (r: EdgeAccessRow) =>
              r.entry ? (
                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-xs">
                    {r.entry.via === "group"
                      ? t("via.group", { groups: r.entry.groups.join(", ") })
                      : t(`via.${r.entry.via}`)}
                  </span>
                  {r.entry.enters === null && r.entry.via !== "unknown" && (
                    <span className="text-muted-foreground text-xs">{t("loginUnchecked")}</span>
                  )}
                </div>
              ) : null,
          } satisfies Column<EdgeAccessRow>,
        ]
      : []),
    {
      id: "setBy",
      header: t("columns.setBy"),
      cellClassName: "max-w-56",
      cell: (r) =>
        r.source === "manifest" ? (
          <span className="font-mono text-xs">astrolift.toml</span>
        ) : r.source === "ui" ? (
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-xs" title={r.setBy ?? undefined}>
              {r.setBy ?? t("notRecorded")}
            </span>
            {r.setAt && (
              <span className="text-muted-foreground font-mono text-xs whitespace-nowrap">
                {fmt.formatDateTime(r.setAt)}
              </span>
            )}
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cellClassName: "max-w-60",
      cell: (r) => <EdgeStatusCell status={r.status} appliedAt={r.edgeAppliedAt} />,
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3 p-6">
      <ListPage<EdgeAccessRow>
        header={{
          crumbs: adminCrumbs("app-access", t("title")),
          title: t("title"),
          context: t("context"),
        }}
        list={list}
        label={t("label")}
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        rowHref={(r) => r.href}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        notice={person ? <PersonLoginsNotice person={person} /> : undefined}
        empty={{
          icon: <LockKeyholeIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
      />
    </div>
  );
}
