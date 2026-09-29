"use client";

/**
 * ListPage — the list archetype (spec 44 §5.1). Every list page is this.
 *
 * A list declares its fields, views, default sort and paging once; the page
 * passes rows, columns and states. Everything else (views as tabs, the
 * filter bar, sort, column chooser, list or cards, selection, paging, the
 * "new" pill, skeleton, empty and error states) is shared.
 *
 * Minimal usage, a screen (pure) fed by a hook:
 *
 *   // runs-list.ts: the declaration, shared by the hook and the screen
 *   export const RUNS_LIST: ListDefinition = {
 *     id: "agents.runs",
 *     fields: [
 *       { key: "status", label: "Status", options: [{ value: "failed", label: "failed" }, …] },
 *       { key: "agent", label: "Agent", async: searchAgents },
 *     ],
 *     searchPlaceholder: "Search runs, ids…",
 *     defaultSort: [{ key: "started", dir: "desc" }],
 *     views: standardViews({ startedBy: "me" }, [
 *       { key: "failed", label: "Failed", filters: { status: "failed" } },
 *     ]),
 *     paging: "cursor",
 *     pageSizes: [25, 50, 100],
 *   };
 *
 *   // use-runs-screen.ts: URL state in, query variables out (spec 44 §5.1 contract)
 *   const list = useListState(RUNS_LIST);
 *   const { data, loading, error, refetch } = useQuery(RUNS, { variables: {
 *     filter: list.filters, search: list.state.q || null, sort: formatSort(list.state.sort),
 *     first: list.state.pageSize, after: list.state.after,
 *   }});
 *
 *   // RunsScreen.tsx
 *   <ListPage
 *     header={{ crumbs, title: "Runs", primaryAction: <Button>Run agent</Button> }}
 *     list={list}
 *     label="Runs"
 *     columns={COLUMNS}                 // Column<Run>[]; sortKey makes a header sortable
 *     rows={runs}
 *     getRowId={(r) => r.id}
 *     rowHref={(r) => `/tasks/${r.id}`}
 *     loading={loading && !data}
 *     error={error}
 *     onRetry={refetch}
 *     empty={{ icon: <BotIcon />, title: "No runs yet", actionHref: "/agents", actionLabel: "Run an agent", learnMoreHref: "/documentation/runs" }}
 *     nextCursor={page?.nextCursor}
 *     bulkActions={(sel) => <Button size="sm" onClick={() => retry(sel.selectedIds)}>Retry</Button>}
 *     rowActions={(r) => <DropdownMenuItem onSelect={() => cancel(r.id)}>Cancel</DropdownMenuItem>}
 *   />
 *
 * Numbered lists pass `totalCount` instead of `nextCursor`. Card view needs
 * `renderCard`; CSV export goes in `menu` (see exportCsv.ts). A live list
 * holds its rows with `useHeldRows` and passes `newRows`. Rules the types
 * cannot hold: the first column carries the row link and is never hidden;
 * a field key must not be one of `RESERVED_PARAMS`; views come from
 * `standardViews`, so All and Mine lead.
 *
 * Embedded (a list on a detail page's tab, e.g. an app's Deployments tab):
 * the one row of tabs there is the entity's, so the list draws no header and
 * its views become a compact picker at the start of the filter bar. Views
 * are still `?view=` in the URL (use `useListState`, as on a routed list):
 *
 *   <ListPage embedded list={list} label="Deployments" columns={…} rows={…} … />
 */

import {
  AlertTriangleIcon,
  CheckIcon,
  ChevronDownIcon,
  MoreHorizontalIcon,
  SearchXIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import {
  type Column,
  DataTable,
  type EmptyStateSpec,
  type RowSelection,
  useRowSelection,
} from "@/components/data-table";
import type { CursorTableController } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ShellHeader, type ShellHeaderProps } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { FilterBar, type FilterBarColumn } from "./FilterBar";
import { ListPagination } from "./ListPagination";
import { NewRowsPill } from "./NewRowsPill";
import type { ListStateController } from "./use-list-state";

/** A routed list owns the page header; an embedded one sits under an entity's tabs. */
type ListPageFrame =
  | {
      /** The page header; the list's views become its tabs. */
      header: Omit<ShellHeaderProps, "tabs" | "tabsAriaLabel">;
      embedded?: false;
    }
  | {
      header?: never;
      /** No header: the views are a picker at the start of the filter bar. */
      embedded: true;
    };

export type ListPageProps<TRow> = ListPageFrame & ListPageBodyProps<TRow>;

interface ListPageBodyProps<TRow> {
  list: ListStateController;
  /** Plural noun: the table caption and the state copy ("Could not load runs"). */
  label: string;
  columns: Column<TRow>[];
  rows: TRow[];
  getRowId: (row: TRow) => string;
  /** The whole row links to the detail. */
  rowHref?: (row: TRow) => string;
  /** Items for the row's `⋯` menu (`DropdownMenuItem`s). */
  rowActions?: (row: TRow) => React.ReactNode;
  /** Declaring this turns on row selection and the "3 selected" bar. */
  bulkActions?: (selection: RowSelection) => React.ReactNode;
  /** Card view; without it the list|cards toggle is hidden. */
  renderCard?: (row: TRow) => React.ReactNode;
  /** First load, with no rows yet. A refetch with rows in hand is `stale`. */
  loading?: boolean;
  /** Rows are on screen but answer the previous question: they fade. */
  stale?: boolean;
  error?: { message: string } | null;
  onRetry?: () => void;
  /** No rows in the default view with no filter: the create action and learn-more. */
  empty: EmptyStateSpec;
  /** Numbered paging: the exact count. Cursor paging: shown where cheap. */
  totalCount?: number | null;
  /** Cursor paging: the count is an estimate ("about 1.2k"). */
  approximateCount?: boolean;
  /** Cursor paging: the next page's cursor; absent on the last page. */
  nextCursor?: string | null;
  /** Live lists: rows waiting behind the pill (see useHeldRows). */
  newRows?: { count: number; onReveal: () => void };
  /** The filter bar's overflow `⋯` menu: CSV export on Admin lists. */
  menu?: React.ReactNode;
  /**
   * Between the header and the filter bar: a one-time reveal or a notice
   * the list's reader must see first (a new token's plaintext). Not a
   * second list, and not a panel of settings.
   */
  notice?: React.ReactNode;
  rowClassName?: (row: TRow) => string | undefined;
}

type Phase = "loading" | "error" | "empty" | "emptyView" | "emptyFiltered" | "ready";

function columnLabel<TRow>(c: Column<TRow>): string {
  return c.label ?? (typeof c.header === "string" ? c.header : c.id);
}

export function ListPage<TRow>({
  header,
  embedded = false,
  list,
  label,
  columns,
  rows,
  getRowId,
  rowHref,
  rowActions,
  bulkActions,
  renderCard,
  loading = false,
  stale = false,
  error,
  onRetry,
  empty,
  totalCount,
  approximateCount,
  nextCursor,
  newRows,
  menu,
  notice,
  rowClassName,
}: ListPageProps<TRow>) {
  const { definition: def, state } = list;
  const selection = useRowSelection();
  const noun = label.toLowerCase();
  const view = def.views.find((v) => v.key === state.view);
  const mode = renderCard ? list.mode : "list";

  const phase: Phase = error
    ? "error"
    : loading
      ? "loading"
      : rows.length > 0
        ? "ready"
        : list.isFiltered
          ? "emptyFiltered"
          : state.view !== def.views[0]?.key
            ? "emptyView"
            : "empty";

  const visible = columns.filter((c, i) => i === 0 || !list.hiddenColumns.includes(c.id));
  const tableColumns: Column<TRow>[] = rowActions
    ? [
        ...visible,
        {
          id: "__actions",
          header: <span className="sr-only">Actions</span>,
          width: "w-10",
          align: "right",
          // Above the row's stretched link, so the menu opens instead of the row.
          cellClassName: "relative z-10",
          cell: (row) => <RowMenu label={label}>{rowActions(row)}</RowMenu>,
        },
      ]
    : visible;

  const barColumns: FilterBarColumn[] = columns.map((c, i) => ({
    id: c.id,
    label: columnLabel(c),
    sortKey: c.sortKey,
    hideable: i !== 0,
  }));

  const emptyView: EmptyStateSpec = {
    icon: empty.icon,
    title: `No ${noun} in ${view?.label ?? "this view"}`,
    description: `Nothing here matches the ${view?.label ?? "current"} view right now.`,
  };

  // DataTable's controller, adapted from the list state. The table draws
  // only itself (chrome="table"); the bar and paging are ours.
  const controller: CursorTableController<TRow> = {
    rows,
    state: phase === "emptyView" ? "empty" : phase === "emptyFiltered" ? "emptyFiltered" : phase,
    error: error ? { name: "Error", message: error.message } : undefined,
    retry: () => onRetry?.(),
    refetch: () => onRetry?.(),
    totalCount: null,
    pageIndex: 0,
    hasNext: false,
    hasPrev: false,
    next: () => {},
    prev: () => {},
    pageSize: state.pageSize,
    setPageSize: list.setPageSize,
    search: state.q,
    setSearch: list.setSearch,
    isStale: stale && rows.length > 0,
    isSearching: false,
    searchEnabled: false,
    sort: state.sort[0],
    toggleSort: (key) => list.toggleSort(key, false),
    sortEnabled: true,
    isFiltered: list.isFiltered,
    clearFilters: list.clearFilters,
  };

  const primary = state.sort[0];
  const sortedBy = columns.find((c) => c.sortKey === primary?.key);
  const order = !primary
    ? undefined
    : primary.dir === "desc" && primary.key === def.defaultSort[0]?.key
      ? "newest first"
      : `by ${sortedBy ? columnLabel(sortedBy) : primary.key} ${primary.dir === "asc" ? "↑" : "↓"}`;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {!embedded && header && (
        <ShellHeader
          {...header}
          tabs={def.views.map((v) => ({
            key: v.key,
            label: v.label,
            href: list.viewHref(v.key),
            active: v.key === state.view,
          }))}
          tabsAriaLabel="Views"
        />
      )}

      {!embedded && view?.note && <ViewNote note={view.note} className="-mt-2" />}
      {notice}
      <FilterBar
        list={list}
        columns={barColumns}
        cards={Boolean(renderCard)}
        menu={menu}
        leading={embedded && def.views.length > 1 ? <ViewPicker list={list} /> : undefined}
      />
      {embedded && view?.note && <ViewNote note={view.note} className="-mt-2" />}

      {newRows && newRows.count > 0 && (
        <NewRowsPill count={newRows.count} onReveal={newRows.onReveal} />
      )}

      {mode === "list" ? (
        <DataTable
          chrome="table"
          label={label}
          controller={controller}
          columns={tableColumns}
          getRowId={getRowId}
          empty={phase === "emptyView" ? emptyView : empty}
          emptyFiltered={{
            title: `No ${noun} match`,
            description: "Remove a filter or change the search to see more.",
          }}
          selection={bulkActions ? selection : undefined}
          bulkActions={bulkActions}
          sorts={state.sort}
          onSortToggle={list.toggleSort}
          rowClassName={rowClassName}
          {...(rowHref ? { rowHref } : ({} as { rowHref?: never }))}
        />
      ) : (
        <CardGrid
          phase={phase}
          noun={noun}
          rows={rows}
          getRowId={getRowId}
          rowHref={rowHref}
          renderCard={renderCard!}
          empty={phase === "emptyView" ? emptyView : empty}
          error={error}
          onRetry={onRetry}
          onClear={list.clearFilters}
          count={Math.min(state.pageSize, 6)}
          stale={stale}
        />
      )}

      {phase === "ready" &&
        (def.paging === "numbered" ? (
          <ListPagination
            mode="numbered"
            page={state.page}
            totalCount={totalCount ?? rows.length}
            onPage={list.setPage}
            pageSize={state.pageSize}
            pageSizes={def.pageSizes}
            onPageSize={list.setPageSize}
          />
        ) : (
          <ListPagination
            mode="cursor"
            shown={rows.length}
            order={order}
            hasNewer={list.hasNewer}
            hasOlder={Boolean(nextCursor)}
            onNewer={list.newer}
            onOlder={() => nextCursor && list.older(nextCursor)}
            totalCount={totalCount}
            approximate={approximateCount}
            pageSize={state.pageSize}
            pageSizes={def.pageSizes}
            onPageSize={list.setPageSize}
          />
        ))}
    </div>
  );
}

function ViewNote({ note, className }: { note: string; className?: string }) {
  return (
    <p className={cn("text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]", className)}>
      {note}
    </p>
  );
}

/** An embedded list's views: one compact menu, each view a link to `?view=`. */
function ViewPicker({ list }: { list: ListStateController }) {
  const { definition: def, state } = list;
  const active = def.views.find((v) => v.key === state.view) ?? def.views[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="max-w-full min-w-0" aria-label="View">
          <span className="text-muted-foreground shrink-0">View:</span>
          <span className="min-w-0 truncate">{active?.label}</span>
          <ChevronDownIcon className="size-3.5 shrink-0" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-44">
        {def.views.map((v) => (
          <DropdownMenuItem key={v.key} asChild>
            <Link
              href={list.viewHref(v.key)}
              aria-current={v.key === state.view ? "page" : undefined}
              className="flex items-center gap-2"
            >
              <CheckIcon
                className={cn("size-3.5", v.key !== state.view && "invisible")}
                aria-hidden
              />
              {v.label}
            </Link>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RowMenu({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7" aria-label={`${label}: row actions`}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Card view, with the same states as the table, in the same frame. */
function CardGrid<TRow>({
  phase,
  noun,
  rows,
  getRowId,
  rowHref,
  renderCard,
  empty,
  error,
  onRetry,
  onClear,
  count,
  stale,
}: {
  phase: Phase;
  noun: string;
  rows: TRow[];
  getRowId: (row: TRow) => string;
  rowHref?: (row: TRow) => string;
  renderCard: (row: TRow) => React.ReactNode;
  empty: EmptyStateSpec;
  error?: { message: string } | null;
  onRetry?: () => void;
  onClear: () => void;
  count: number;
  stale: boolean;
}) {
  const grid = "grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-3";
  switch (phase) {
    case "loading":
      return (
        <div className={grid} aria-busy>
          {Array.from({ length: count }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-md" />
          ))}
        </div>
      );
    case "error":
      return (
        <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
          <AlertTriangleIcon className="text-danger size-5" />
          <div className="min-w-0 px-6">
            <p className="font-medium">Could not load {noun}</p>
            <p className="text-muted-foreground mt-1 max-w-md text-sm [overflow-wrap:anywhere]">
              {error?.message ?? "The request failed."}
            </p>
          </div>
          {onRetry && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          )}
        </div>
      );
    case "emptyFiltered":
      return (
        <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
          <SearchXIcon className="text-muted-foreground size-5" />
          <p className="font-medium">No {noun} match</p>
          <Button size="sm" variant="outline" onClick={onClear}>
            Clear filters
          </Button>
        </div>
      );
    case "empty":
    case "emptyView":
      return <EmptyState {...empty} />;
    case "ready":
      return (
        <ul className={cn(grid, "transition-opacity", stale && "opacity-60")}>
          {rows.map((row) => (
            <li key={getRowId(row)} className="min-w-0">
              {rowHref ? (
                <Link
                  href={rowHref(row)}
                  className="focus-visible:ring-ring block h-full rounded-md focus-visible:ring-2 focus-visible:outline-none"
                >
                  {renderCard(row)}
                </Link>
              ) : (
                renderCard(row)
              )}
            </li>
          ))}
        </ul>
      );
  }
}
