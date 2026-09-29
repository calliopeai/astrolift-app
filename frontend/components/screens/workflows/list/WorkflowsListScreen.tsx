"use client";

import Link from "next/link";
import {
  ActivityIcon,
  ClipboardListIcon,
  CopyIcon,
  LayoutTemplateIcon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  PowerIcon,
  PowerOffIcon,
  TimerIcon,
  TrashIcon,
  WorkflowIcon,
} from "lucide-react";

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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { VizLegend } from "@/components/viz/core/VizLegend";
import { formatTriggerKind } from "@/components/screens/workflows/detail/workflow-run-state";
import { formatRelativeAge } from "@/lib/format";

import { WORKFLOW_GLYPH_LEGEND, WorkflowGlyph } from "./WorkflowGlyph";
import {
  LAST_RUN_DOT,
  LAST_RUN_LABEL,
  type WorkflowRow,
  workflowPatternLabel,
  workflowsCrumbs,
} from "./workflows-list";

export interface WorkflowsListScreenProps {
  list: ListStateController;
  /** The page on screen, already filtered, searched, sorted and sliced. */
  rows: WorkflowRow[];
  /** Workflows matching the view, filters and search, across all pages. */
  totalCount: number;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** The configured-workflow page cap, when the org has more than it. */
  truncatedAt: number | null;
  /** The workflows module's grants: New workflow and clone; enable, disable and delete; Run. */
  canCreate: boolean;
  canManage: boolean;
  canRun: boolean;
  /** `audit_log.read`: the platform's Temporal instances. */
  canViewPlatformRuns: boolean;
  /** The row with a mutation in flight. */
  busySlug: string | null;
  onRun: (row: WorkflowRow) => Promise<void>;
  onToggle: (row: WorkflowRow) => Promise<void>;
  onDelete: (row: WorkflowRow) => Promise<void>;
  onClone: (row: WorkflowRow) => Promise<void>;
}

const workflowHref = (w: Pick<WorkflowRow, "slug">, tail = "") =>
  `/workflows/${encodeURIComponent(w.slug)}${tail}`;

/** Where the workflow comes from, in one muted line. */
function Origin({ row }: { row: WorkflowRow }) {
  const text =
    row.kind === "configured"
      ? `from ${row.definitionName}`
      : row.kind === "template"
        ? "Platform template"
        : (row.sourcePath ?? "Organization definition");
  return (
    <span
      className={`text-muted-foreground block truncate text-xs ${row.sourcePath ? "font-mono" : ""}`}
      title={text}
    >
      {text}
    </span>
  );
}

function Name({ row }: { row: WorkflowRow }) {
  return (
    <span className="block min-w-0">
      <span className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate font-medium" title={row.name}>
          {row.name}
        </span>
        {!row.isEnabled && (
          <Badge variant="secondary" className="shrink-0">
            Disabled
          </Badge>
        )}
      </span>
      <Origin row={row} />
    </span>
  );
}

function LastRun({ row }: { row: WorkflowRow }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs">
      <StatusDot status={LAST_RUN_DOT[row.lastRun]} />
      <span className="truncate">{LAST_RUN_LABEL[row.lastRun]}</span>
      {row.lastRunAt && (
        <span className="text-muted-foreground shrink-0 font-mono" title={row.lastRunAt}>
          {formatRelativeAge(row.lastRunAt)}
        </span>
      )}
    </span>
  );
}

function Trigger({ row }: { row: WorkflowRow }) {
  if (row.triggerKind === null)
    return <span className="text-muted-foreground text-xs">Manual</span>;
  return (
    <span className="block min-w-0">
      <span className="block truncate text-sm">{formatTriggerKind(row.triggerKind)}</span>
      {row.scheduleCron && (
        <span className="text-muted-foreground block truncate font-mono text-xs">
          {row.scheduleCron}
        </span>
      )}
    </span>
  );
}

function Stages({ row }: { row: WorkflowRow }) {
  return row.stageCount === null ? (
    <span className="text-muted-foreground text-xs">unknown</span>
  ) : (
    <span className="font-mono text-xs tabular-nums">{row.stageCount}</span>
  );
}

/** A card: the stage-shape glyph, name, pattern, stage count and last run. */
function WorkflowCard({ row }: { row: WorkflowRow }) {
  return (
    <div className="bg-card hover:bg-accent/30 flex h-full min-w-0 flex-col gap-3 rounded-md border p-4 transition-colors">
      <div className="min-w-0">
        <Name row={row} />
      </div>
      <WorkflowGlyph line={row.line} />
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">{workflowPatternLabel(row.patternKind)}</Badge>
        {row.stageCount !== null && (
          <span className="text-muted-foreground font-mono tabular-nums">
            {row.stageCount} {row.stageCount === 1 ? "stage" : "stages"}
          </span>
        )}
        {row.projectSlug && (
          <span
            className="text-muted-foreground min-w-0 truncate font-mono"
            title={row.projectSlug}
          >
            {row.projectSlug}
          </span>
        )}
      </div>
      <div className="mt-auto min-w-0">
        <LastRun row={row} />
      </div>
    </div>
  );
}

/** The header's `⋯`: workflow runs, and the platform's Temporal instances for audit readers. */
function HeaderMenu({ canViewPlatformRuns }: { canViewPlatformRuns: boolean }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label="More workflow actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        <DropdownMenuItem asChild>
          <Link href="/tasks?kind=workflow">
            <ClipboardListIcon className="size-4" />
            Workflow runs
          </Link>
        </DropdownMenuItem>
        {canViewPlatformRuns && (
          <DropdownMenuItem asChild>
            <Link href="/workflows/instances">
              <ActivityIcon className="size-4" />
              Platform instances
            </Link>
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Agents › Workflows (spec 44 §5.1, §4.4): every workflow on the shared list,
 * views All · Mine · Templates, pattern, project and last-run filters,
 * numbered pages, list or cards (each card draws the stage shape). The page
 * is only this list (Leo's rule 3): runs are Agents › Runs, the platform's
 * Temporal instances their own page, and a workflow's builder, runs,
 * triggers and settings its tabs. Pure view; the data half is useWorkflowsList.
 */
export function WorkflowsListScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  truncatedAt,
  canCreate,
  canManage,
  canRun,
  canViewPlatformRuns,
  busySlug,
  onRun,
  onToggle,
  onDelete,
  onClone,
}: WorkflowsListScreenProps) {
  const empty: EmptyStateSpec = {
    icon: <WorkflowIcon className="size-5" />,
    title: "No workflows",
    description:
      "Author a workflow from a pattern or a manifest, configure a platform template, or import one from an agent repository.",
    ...(canCreate ? { actionHref: "/workflows/new", actionLabel: "New workflow" } : {}),
    learnMoreHref: "https://github.com/calliopeai/astrolift-docs/blob/main/reference/workflows.md",
  };

  const columns: Column<WorkflowRow>[] = [
    {
      id: "workflow",
      header: "Workflow",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (w) => <Name row={w} />,
    },
    {
      id: "pattern",
      header: "Pattern",
      sortKey: "pattern",
      cell: (w) => <span className="text-sm">{workflowPatternLabel(w.patternKind)}</span>,
    },
    { id: "stages", header: "Stages", sortKey: "stages", cell: (w) => <Stages row={w} /> },
    {
      id: "project",
      header: "Project",
      cellClassName: "max-w-48",
      cell: (w) =>
        w.projectSlug ? (
          <span className="block truncate font-mono text-xs" title={w.projectSlug}>
            {w.projectSlug}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    { id: "trigger", header: "Trigger", cell: (w) => <Trigger row={w} /> },
    { id: "lastRun", header: "Last run", sortKey: "lastRun", cell: (w) => <LastRun row={w} /> },
  ];

  const notices = (
    <>
      {truncatedAt !== null && (
        <p className="text-muted-foreground text-xs">
          Showing the newest <span className="font-mono">{truncatedAt}</span> configured workflows;
          the list does not page past that yet.
        </p>
      )}
      {list.mode === "card" && <VizLegend items={WORKFLOW_GLYPH_LEGEND} motion="full" />}
    </>
  );

  return (
    <ListPage<WorkflowRow>
      header={{
        crumbs: workflowsCrumbs(),
        title: "Workflows",
        primaryAction: canCreate ? (
          <Button size="sm" asChild>
            <Link href="/workflows/new">
              <PlusIcon className="size-4" />
              New workflow
            </Link>
          </Button>
        ) : undefined,
        menu: <HeaderMenu canViewPlatformRuns={canViewPlatformRuns} />,
      }}
      list={list}
      label="Workflows"
      columns={columns}
      rows={rows}
      getRowId={(w) => w.id}
      rowHref={(w) => workflowHref(w)}
      renderCard={(w) => <WorkflowCard row={w} />}
      rowActions={(w) => {
        const busy = busySlug === w.slug;
        return (
          <>
            {canRun && w.kind !== "template" && (
              <DropdownMenuItem disabled={busy || !w.isEnabled} onSelect={() => void onRun(w)}>
                <PlayIcon className="size-4" />
                Run now
              </DropdownMenuItem>
            )}
            {canManage && w.kind === "configured" && (
              <DropdownMenuItem disabled={busy} onSelect={() => void onToggle(w)}>
                {w.isEnabled ? (
                  <PowerOffIcon className="size-4" />
                ) : (
                  <PowerIcon className="size-4" />
                )}
                {w.isEnabled ? "Disable" : "Enable"}
              </DropdownMenuItem>
            )}
            {w.kind === "configured" && (
              <DropdownMenuItem asChild>
                <Link href={workflowHref(w, "/triggers")}>
                  <TimerIcon className="size-4" />
                  Edit triggers
                </Link>
              </DropdownMenuItem>
            )}
            {canCreate && w.kind !== "configured" && (
              <DropdownMenuItem asChild>
                <Link href={`/workflows/new?definition=${encodeURIComponent(w.slug)}`}>
                  <LayoutTemplateIcon className="size-4" />
                  Create workflow from
                </Link>
              </DropdownMenuItem>
            )}
            {canCreate && w.kind === "template" && (
              <DropdownMenuItem disabled={busy} onSelect={() => void onClone(w)}>
                <CopyIcon className="size-4" />
                Clone to edit
              </DropdownMenuItem>
            )}
            {canManage && w.deletable && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={busy}
                  className="text-destructive focus:text-destructive"
                  onSelect={() => void onDelete(w)}
                >
                  <TrashIcon className="size-4" />
                  Delete
                </DropdownMenuItem>
              </>
            )}
          </>
        );
      }}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={empty}
      totalCount={totalCount}
      notice={notices}
    />
  );
}
