"use client";

import { BoxIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { useFormatters } from "@/lib/i18n/formatters";

import type { ToolRegistryState, ToolRegistryTool } from "./use-tool-registry";
import { localizedToolAdapter, localizedToolsCrumbs } from "./tools-list";

export type ToolRegistryScreenProps = ToolRegistryState;

/**
 * Agents › Tools (spec 44 §4.4, §5.1): every tool definition across every
 * skill in the org on the shared list, views All · Mine · Built-in · Custom,
 * skill and adapter filters, numbered pages. Tools are registered on a skill's Tools
 * tab, so the page has no create action of its own. Pure view; the data
 * half is useToolRegistry.
 */
export function ToolRegistryScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
}: ToolRegistryScreenProps) {
  const fmt = useFormatters();
  const label = useTranslations("agentToolRegistry");

  const columns: Column<ToolRegistryTool>[] = [
    {
      id: "tool",
      header: label("tool"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (t) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={t.name}>
            {t.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs" title={t.slug}>
            {t.slug}
          </span>
        </span>
      ),
    },
    {
      id: "skill",
      header: label("skill"),
      sortKey: "skill",
      cellClassName: "max-w-56",
      cell: (t) =>
        t.skillSlug ? (
          <span className="block min-w-0">
            <span className="block truncate text-sm" title={t.skillName || t.skillSlug}>
              {t.skillName || t.skillSlug}
            </span>
            <span className="text-muted-foreground block truncate text-xs">
              {t.isBuiltin
                ? label("builtin")
                : t.skillIsGlobal
                  ? label("global")
                  : label("organization")}
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{label("none")}</span>
        ),
    },
    {
      id: "adapter",
      header: label("adapter"),
      sortKey: "adapter",
      cell: (t) =>
        t.adapter ? (
          <Badge variant="outline">{localizedToolAdapter(label, t.adapter)}</Badge>
        ) : (
          <span className="text-muted-foreground text-xs">{label("unknown")}</span>
        ),
    },
    {
      id: "description",
      header: label("description"),
      cellClassName: "max-w-80",
      cell: (t) =>
        t.description ? (
          <span className="text-muted-foreground line-clamp-2 text-sm [overflow-wrap:anywhere]">
            {t.description}
          </span>
        ) : (
          <span className="text-muted-foreground text-sm italic">{label("noDescription")}</span>
        ),
    },
    {
      id: "handler",
      header: label("handler"),
      cellClassName: "max-w-64",
      cell: (t) =>
        t.handlerRef ? (
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={t.handlerRef}
          >
            {t.handlerRef}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{label("none")}</span>
        ),
    },
    {
      id: "created",
      header: label("registered"),
      sortKey: "created",
      cell: (t) => (
        <span className="text-muted-foreground font-mono text-xs" title={t.createdAt}>
          {t.createdAt && Number.isFinite(new Date(t.createdAt).getTime())
            ? fmt.formatDateTime(t.createdAt)
            : label("unknownDate")}
        </span>
      ),
    },
  ];

  return (
    <ListPage<ToolRegistryTool>
      header={{
        crumbs: localizedToolsCrumbs(label),
        title: label("title"),
        context: label("context"),
      }}
      list={list}
      label={label("title")}
      columns={columns}
      rows={rows}
      getRowId={(t) => t.id}
      rowHref={(t) => `/agents/tools/${t.id}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BoxIcon className="size-5" />,
        title: label("emptyTitle"),
        description: label("emptyDescription"),
        actionHref: "/agents/skills",
        actionLabel: label("goToSkills"),
      }}
      totalCount={totalCount}
    />
  );
}
