"use client";

import { BoxIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { ADAPTER_LABEL } from "@/components/screens/agents/skills/tool-adapters";
import { Badge } from "@/components/ui/badge";
import { useFormatters } from "@/lib/i18n/formatters";

import type { ToolRegistryState, ToolRegistryTool } from "./use-tool-registry";

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

  const columns: Column<ToolRegistryTool>[] = [
    {
      id: "tool",
      header: "Tool",
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
      header: "Skill",
      sortKey: "skill",
      cellClassName: "max-w-56",
      cell: (t) =>
        t.skillSlug ? (
          <span className="block min-w-0">
            <span className="block truncate text-sm" title={t.skillName || t.skillSlug}>
              {t.skillName || t.skillSlug}
            </span>
            <span className="text-muted-foreground block truncate text-xs">
              {t.isBuiltin ? "Built-in" : t.skillIsGlobal ? "Global" : "Organization"}
            </span>
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    {
      id: "adapter",
      header: "Adapter",
      sortKey: "adapter",
      cell: (t) =>
        t.adapter ? (
          <Badge variant="outline">{ADAPTER_LABEL[t.adapter] ?? t.adapter}</Badge>
        ) : (
          <span className="text-muted-foreground text-xs">unknown</span>
        ),
    },
    {
      id: "description",
      header: "Description",
      cellClassName: "max-w-80",
      cell: (t) =>
        t.description ? (
          <span className="text-muted-foreground line-clamp-2 text-sm [overflow-wrap:anywhere]">
            {t.description}
          </span>
        ) : (
          <span className="text-muted-foreground text-sm italic">No description</span>
        ),
    },
    {
      id: "handler",
      header: "Handler",
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
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    {
      id: "created",
      header: "Registered",
      sortKey: "created",
      cell: (t) => (
        <span className="text-muted-foreground font-mono text-xs">
          {t.createdAt ? fmt.formatDateTime(t.createdAt) : "unknown"}
        </span>
      ),
    },
  ];

  return (
    <ListPage<ToolRegistryTool>
      header={{
        crumbs: agentsCrumbs("tools"),
        title: "Tools",
        context: "Every tool definition across every skill in this org. Register tools on a skill.",
      }}
      list={list}
      label="Tools"
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
        title: "No tools registered",
        description:
          "Register tool definitions on a skill's Tools tab. Tools declare the callable capabilities agents can invoke.",
        actionHref: "/agents/skills",
        actionLabel: "Go to skills",
      }}
      totalCount={totalCount}
    />
  );
}
