"use client";

import { BoxesIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { Badge } from "@/components/ui/badge";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { WorkloadsListState } from "./use-workloads-list";
import { scaleLabel } from "./workloads-list";

export type WorkloadsScreenProps = WorkloadsListState;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

const columnsFor = (t: ReturnType<typeof useTranslations>): Column<AstroliftWorkload>[] => [
  {
    id: "workload",
    header: t("columns.workload"),
    sortKey: "name",
    cellClassName: "max-w-80",
    cell: (w) => (
      <span className="block min-w-0">
        <span className="block truncate font-medium" title={w.name}>
          {w.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={w.slug}>
          {w.slug}
        </span>
      </span>
    ),
  },
  {
    id: "kind",
    header: t("columns.kind"),
    sortKey: "kind",
    cell: (w) => (
      <Badge variant="outline">{t.has(`kinds.${w.kind}`) ? t(`kinds.${w.kind}`) : w.kind}</Badge>
    ),
  },
  {
    id: "app",
    header: t("columns.app"),
    sortKey: "app",
    cellClassName: `${ABOVE_ROW_LINK} max-w-64`,
    cell: (w) => (
      <Link
        href={`/apps/${w.registeredAppSlug}`}
        className="block truncate font-mono text-xs hover:underline"
        title={w.registeredAppSlug}
      >
        {w.registeredAppSlug}
      </Link>
    ),
  },
  {
    id: "scale",
    header: t("columns.replicas"),
    align: "right",
    cell: (w) => <span className="font-mono text-xs">{scaleLabel(w)}</span>,
  },
  {
    id: "schedule",
    header: t("columns.schedule"),
    cell: (w) =>
      w.schedule ? (
        <span className="font-mono text-xs">{w.schedule}</span>
      ) : (
        <span className="text-muted-foreground text-xs">{t("none")}</span>
      ),
  },
];

/**
 * Agents › Workloads (spec 44 §4.1, §10.2): the runtime units behind
 * agents, workflows and functions on the shared list, views All · Mine ·
 * Agents · Workflows · Functions. Each row opens the workload on its app.
 * Pure view; the data half is useWorkloadsList.
 */
export function WorkloadsScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: WorkloadsScreenProps) {
  const t = useTranslations("workloadsScreen");
  return (
    <ListPage<AstroliftWorkload>
      header={{
        crumbs: agentsCrumbs("workloads"),
        title: t("title"),
        context: t("context"),
      }}
      list={list}
      label={t("title")}
      columns={columnsFor(t)}
      rows={rows}
      getRowId={(w) => w.id}
      rowHref={(w) => `/apps/${w.registeredAppSlug}/workloads/${w.slug}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BoxesIcon className="size-5" />,
        title: t("empty.title"),
        description: t("empty.description"),
        actionHref: "/agents",
        actionLabel: t("empty.action"),
      }}
      totalCount={totalCount}
    />
  );
}
