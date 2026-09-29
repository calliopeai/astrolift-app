"use client";

import { CodeIcon, PlusIcon, TrashIcon } from "lucide-react";
import Link from "next/link";
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
import type { ToolDef } from "./use-skill-tool-defs";
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
  const [toRemove, setToRemove] = useState<ToolDef | null>(null);
  const newHref = `${skillTabHref(id, "tools")}/new`;

  const columns: Column<ToolDef>[] = [
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
          <span className="text-muted-foreground block truncate text-xs" title={t.description}>
            {t.description || <span className="italic">No description</span>}
          </span>
        </span>
      ),
    },
    {
      id: "adapter",
      header: "Adapter",
      sortKey: "adapter",
      cell: (t) => <Badge variant="secondary">{ADAPTER_LABEL[t.adapter] ?? t.adapter}</Badge>,
    },
    {
      id: "handler",
      header: "Handler",
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
            Register tool
          </Link>
        </Button>
      }
    >
      <ListPage<ToolDef>
        embedded
        list={list}
        label="Tools"
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
            Remove
          </DropdownMenuItem>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <CodeIcon className="size-5" />,
          title: "No tool definitions",
          description: "Register tool definitions to give this skill executable capabilities.",
          actionHref: newHref,
          actionLabel: "Register first tool",
        }}
        totalCount={totalCount}
      />

      <ConfirmDialog
        open={toRemove !== null}
        onOpenChange={(next) => {
          if (!next) setToRemove(null);
        }}
        title={toRemove ? `Remove tool "${toRemove.name}"?` : "Remove tool?"}
        description="The tool definition is deleted from this skill. Agents lose the capability on their next run."
        confirmLabel="Remove tool"
        destructive
        onConfirm={async () => {
          if (toRemove) await removeTool(toRemove.id);
        }}
      />
    </SkillFrame>
  );
}
