"use client";

import { CodeIcon, PlusIcon, TrashIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { SkillFrame, skillTabHref } from "./SkillFrame";
import { ADAPTER_LABEL } from "./tool-adapters";
import type { SkillToolRow } from "./skill-tools-list";
import type { SkillToolsState } from "./use-skill-tools";

export type SkillToolsScreenProps = SkillToolsState;

/**
 * A skill's Tools tab (spec 44 §5.2): its tool definitions on the embedded
 * list, each row to the tool's detail, Remove in the row's `⋯`, and Register
 * tool, a stepped page, as the tab's primary action. Pure view; the data
 * half is useSkillTools.
 */
export function SkillToolsScreen({
  id,
  skill,
  skillLoading,
  skillError,
  onSkillRetry,
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  removing,
  removeTool,
}: SkillToolsScreenProps) {
  const fmt = useFormatters();
  const tr = useTranslations("agentSkills.skillTools");
  const common = useTranslations("agentSkills.common");
  const adapters = useTranslations("agentToolRegistry");
  const [toRemove, setToRemove] = useState<SkillToolRow | null>(null);
  const newHref = `${skillTabHref(id, "tools")}/new`;

  const columns: Column<SkillToolRow>[] = [
    {
      id: "tool",
      header: tr("tool"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (t) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={t.name}>
            {t.name}
          </span>
          <span className="text-muted-foreground block truncate text-xs" title={t.description}>
            {t.description || <span className="italic">{common("noDescription")}</span>}
          </span>
        </span>
      ),
    },
    {
      id: "adapter",
      header: tr("adapter"),
      sortKey: "adapter",
      cell: (t) => (
        <Badge variant="secondary">
          {ADAPTER_LABEL[t.adapter] ? adapters(t.adapter) : t.adapter}
        </Badge>
      ),
    },
    {
      id: "handler",
      header: tr("handler"),
      cellClassName: "max-w-72",
      cell: (t) =>
        t.handlerRef ? (
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={t.handlerRef}
          >
            {t.handlerRef}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{common("none")}</span>
        ),
    },
    {
      id: "created",
      header: tr("registered"),
      sortKey: "created",
      cell: (t) => (
        <span className="text-muted-foreground font-mono text-xs">
          {t.createdAt ? fmt.formatDateTime(t.createdAt) : common("unknown")}
        </span>
      ),
    },
  ];

  return (
    <SkillFrame
      id={id}
      active="tools"
      skill={skill}
      loading={skillLoading}
      error={skillError}
      onRetry={onSkillRetry}
      primaryAction={
        <Button size="sm" asChild>
          <Link href={newHref}>
            <PlusIcon className="size-4" />
            {common("registerTool")}
          </Link>
        </Button>
      }
    >
      <ListPage<SkillToolRow>
        embedded
        list={list}
        label={common("tools")}
        columns={columns}
        rows={rows}
        getRowId={(t) => t.id}
        rowHref={(t) => `/agents/tools/${t.id}`}
        rowActions={(t) => (
          <DropdownMenuItem
            variant="destructive"
            disabled={removing}
            onSelect={() => setToRemove(t)}
          >
            <TrashIcon className="size-4" />
            {tr("remove")}
          </DropdownMenuItem>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <CodeIcon className="size-5" />,
          title: common("noTools"),
          description: common("noToolsDescription"),
          actionHref: newHref,
          actionLabel: common("registerFirstTool"),
        }}
        totalCount={totalCount}
      />

      <ConfirmDialog
        open={toRemove !== null}
        onOpenChange={(next) => {
          if (!next) setToRemove(null);
        }}
        title={toRemove ? tr("removeTitle", { name: toRemove.name }) : tr("removeTitleGeneric")}
        description={tr("removeDescription")}
        confirmLabel={tr("removeConfirm")}
        destructive
        onConfirm={async () => {
          if (toRemove) await removeTool(toRemove.id);
        }}
      />
    </SkillFrame>
  );
}
