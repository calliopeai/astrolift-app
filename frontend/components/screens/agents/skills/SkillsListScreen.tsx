"use client";

import { BookOpenIcon, GitBranchIcon, MoreHorizontalIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { agentsCrumbs } from "./catalog";
import type { SkillListItem } from "./skills-list";

export interface SkillsListScreenProps {
  list: ListStateController;
  /** The page on screen, already filtered, sorted and sliced. */
  rows: SkillListItem[];
  /** Skills matching the view, filters and search, across all pages. */
  totalCount: number;
  loading: boolean;
  /** Rows on screen answer the previous list state while the next loads. */
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** Opens the Import from repo sheet (`?import=1`). */
  onImportOpenChange: (open: boolean) => void;
  /** The Import from repo sheet, rendered by the route with its own hook. */
  importSheet?: React.ReactNode;
}

/**
 * Agents › Skills (spec 44 §4.4, §5.1): the skill registry on the shared
 * list, views All · Mine · Imported, status and scope filters, numbered
 * pages. New skill is a stepped page; Import from repo is a sheet in `⋯`.
 * Pure view; the data half is useSkillsList.
 */
export function SkillsListScreen({
  list,
  rows,
  totalCount,
  loading,
  stale = false,
  error,
  onRetry,
  onImportOpenChange,
  importSheet,
}: SkillsListScreenProps) {
  const fmt = useFormatters();

  const empty: EmptyStateSpec = {
    icon: <BookOpenIcon className="size-5" />,
    title: "No skills defined",
    description: "Skills package instructions and tools that agents draw on when running tasks.",
    actionHref: "/agents/skills/new",
    actionLabel: "New skill",
  };

  const columns: Column<SkillListItem>[] = [
    {
      id: "skill",
      header: "Skill",
      sortKey: "name",
      cellClassName: "max-w-96",
      cell: (s) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={s.name}>
            {s.name}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs" title={s.slug}>
            {s.slug}
          </span>
        </span>
      ),
    },
    {
      id: "description",
      header: "Description",
      cellClassName: "max-w-80",
      cell: (s) =>
        s.description ? (
          <span className="text-muted-foreground line-clamp-2 text-sm [overflow-wrap:anywhere]">
            {s.description}
          </span>
        ) : (
          <span className="text-muted-foreground text-sm italic">No description</span>
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (s) => (
        <span className="inline-flex items-center gap-1.5 text-sm">
          <StatusDot status={s.isActive ? "ok" : "muted"} />
          {s.isActive ? "Active" : "Inactive"}
        </span>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (s) =>
        s.isGlobal ? (
          <Badge variant="outline">Global</Badge>
        ) : (
          <span className="text-muted-foreground text-sm">Organization</span>
        ),
    },
    {
      id: "version",
      header: "Version",
      sortKey: "version",
      cell: (s) => <span className="font-mono text-xs">v{s.skillVersion}</span>,
    },
    {
      id: "updated",
      header: "Updated",
      sortKey: "updated",
      cell: (s) => (
        <span className="text-muted-foreground font-mono text-xs">
          {s.updatedAt ? fmt.formatDateTime(s.updatedAt) : "unknown"}
        </span>
      ),
    },
  ];

  return (
    <>
      <ListPage<SkillListItem>
        header={{
          crumbs: agentsCrumbs("skills"),
          title: "Skills",
          context: "Versioned instructions and tool bindings your agents draw on at dispatch time.",
          primaryAction: (
            <Button size="sm" asChild>
              <Link href="/agents/skills/new">
                <PlusIcon className="size-4" />
                New skill
              </Link>
            </Button>
          ),
          menu: (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon" variant="ghost" className="size-8" aria-label="More actions">
                  <MoreHorizontalIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-48">
                <DropdownMenuItem onSelect={() => onImportOpenChange(true)}>
                  <GitBranchIcon className="size-4" />
                  Import from repo
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ),
        }}
        list={list}
        label="Skills"
        columns={columns}
        rows={rows}
        getRowId={(s) => s.id}
        rowHref={(s) => `/agents/skills/${s.id}`}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={empty}
        totalCount={totalCount}
      />
      {importSheet}
    </>
  );
}
