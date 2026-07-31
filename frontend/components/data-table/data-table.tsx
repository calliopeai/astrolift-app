"use client";

/**
 * DataTable — the house standard for every list surface (#1231).
 *
 * The previous standard was documented in frontend/bootstrap.md and had
 * zero consumers; 100+ surfaces shipped past it without anyone filing an
 * issue. Documentation alone demonstrably failed, so the guarantees that
 * matter are encoded in the prop types instead of in prose:
 *
 *   - `label` and `empty` are required, which kills the "renders nothing
 *     when the list is empty" variants.
 *   - `emptyFiltered` is distinct from `empty`: "you have no clusters yet"
 *     and "no cluster matches 'prod'" want different words and different
 *     actions, and conflating them tells first-run operators their search
 *     is broken.
 *   - `rowHref` and `onRowActivate` are mutually exclusive by type. Rows
 *     that navigate must be real links (middle-click, open-in-new-tab,
 *     copy-link all work); rows that do something else must not pretend
 *     to be links.
 *
 * The eslint rule `astrolift/no-raw-table` keeps `@/components/ui/table`
 * from being imported anywhere but this directory, because that is the
 * part the last attempt was missing.
 */

import * as React from "react";
import Link from "next/link";
import { AlertTriangleIcon, SearchXIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

import { DataTablePagination } from "./data-table-pagination";
import { DataTableToolbar } from "./data-table-toolbar";
import { SortableColumnHeader } from "./sortable-column-header";
import type { Column, EmptyStateSpec } from "./types";
import type { CursorTableController } from "./use-cursor-table";
import type { RowSelection } from "./use-row-selection";

type RowActivation<TRow> =
  | { rowHref: (row: TRow) => string; onRowActivate?: never }
  | { onRowActivate: (row: TRow) => void; rowHref?: never }
  | { rowHref?: never; onRowActivate?: never };

type DataTableBaseProps<TRow> = {
  /** Names the table for screen readers and for the bulk-selection copy. */
  label: string;
  controller: CursorTableController<TRow>;
  columns: Column<TRow>[];
  getRowId: (row: TRow) => string;
  /** Shown when the list is genuinely empty. Required. */
  empty: EmptyStateSpec;
  /** Shown when filters excluded everything. Defaults to a clear-search prompt. */
  emptyFiltered?: Pick<EmptyStateSpec, "title" | "description">;
  selection?: RowSelection;
  /** Rendered in a bar above the table while rows are selected. */
  bulkActions?: (selection: RowSelection) => React.ReactNode;
  searchPlaceholder?: string;
  /** Filters and actions for the toolbar row. */
  toolbar?: React.ReactNode;
  /** Extra classes on the row, e.g. to tint a failed one. */
  rowClassName?: (row: TRow) => string | undefined;
  className?: string;
};

export type DataTableProps<TRow> = DataTableBaseProps<TRow> & RowActivation<TRow>;

const ALIGN: Record<NonNullable<Column<unknown>["align"]>, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function DataTable<TRow>({
  label,
  controller,
  columns,
  getRowId,
  empty,
  emptyFiltered,
  selection,
  bulkActions,
  searchPlaceholder,
  toolbar,
  rowHref,
  onRowActivate,
  rowClassName,
  className,
}: DataTableProps<TRow>) {
  const { rows, state, error, retry, pageSize, isFiltered, clearFilters } = controller;

  const pageIds = React.useMemo(() => rows.map(getRowId), [rows, getRowId]);
  const columnCount = columns.length + (selection ? 1 : 0);

  const body = (() => {
    switch (state) {
      case "loading":
        // Skeletons render inside the real table, sized from the column
        // defs, so the first paint has the geometry the rows will have and
        // nothing jumps when they arrive.
        return Array.from({ length: Math.min(pageSize, 8) }).map((_, i) => (
          <TableRow key={`skeleton-${i}`}>
            {selection && (
              <TableCell className="w-10">
                <Skeleton className="size-4" />
              </TableCell>
            )}
            {columns.map((column) => (
              <TableCell key={column.id} className={column.width}>
                <Skeleton className="h-4 w-full" />
              </TableCell>
            ))}
          </TableRow>
        ));

      case "error":
        return (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={columnCount} className="py-10">
              <div className="flex flex-col items-center gap-3 text-center">
                <AlertTriangleIcon className="text-danger size-5" />
                <div>
                  <p className="font-medium">Could not load {label.toLowerCase()}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {error?.message ?? "The request failed."}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={retry}>
                  Retry
                </Button>
              </div>
            </TableCell>
          </TableRow>
        );

      case "emptyFiltered":
        return (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={columnCount} className="py-10">
              <div className="flex flex-col items-center gap-3 text-center">
                <SearchXIcon className="text-muted-foreground size-5" />
                <div>
                  <p className="font-medium">{emptyFiltered?.title ?? "No matches"}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {emptyFiltered?.description ??
                      `No ${label.toLowerCase()} match the current filters.`}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  Clear search
                </Button>
              </div>
            </TableCell>
          </TableRow>
        );

      case "empty":
        return (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={columnCount} className="p-0">
              <EmptyState {...empty} />
            </TableCell>
          </TableRow>
        );

      case "ready":
        return rows.map((row) => {
          const id = getRowId(row);
          const selected = selection?.isSelected(id) ?? false;
          return (
            <TableRow
              key={id}
              data-state={selected ? "selected" : undefined}
              className={cn(
                (rowHref || onRowActivate) && "cursor-pointer",
                rowClassName?.(row)
              )}
              onClick={onRowActivate ? () => onRowActivate(row) : undefined}
            >
              {selection && (
                <TableCell className="w-10" onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selected}
                    onCheckedChange={() => selection.toggle(id)}
                    aria-label={`Select row ${id}`}
                  />
                </TableCell>
              )}
              {columns.map((column) => (
                <TableCell
                  key={column.id}
                  className={cn(
                    column.width,
                    column.align && ALIGN[column.align],
                    column.cellClassName
                  )}
                >
                  {/* One link per row, stretched over the cell, so the whole
                      row is clickable without nesting an anchor per cell. */}
                  {rowHref && column.id === columns[0].id ? (
                    <Link href={rowHref(row)} className="after:absolute after:inset-0">
                      {column.cell(row)}
                    </Link>
                  ) : (
                    column.cell(row)
                  )}
                </TableCell>
              ))}
            </TableRow>
          );
        });
    }
  })();

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <DataTableToolbar controller={controller} searchPlaceholder={searchPlaceholder}>
        {toolbar}
      </DataTableToolbar>

      {selection && selection.selectedCount > 0 && bulkActions && (
        <div className="bg-muted/50 flex flex-wrap items-center gap-3 rounded-md border px-3 py-2">
          <span className="text-sm font-medium tabular-nums">
            {selection.selectedCount} selected
          </span>
          <Button variant="ghost" size="sm" onClick={selection.clear}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {bulkActions(selection)}
          </div>
        </div>
      )}

      {/* Rows persist across a refetch rather than blanking, so fade them
          while they are answering the previous question. */}
      <div
        className={cn(
          "rounded-md border transition-opacity",
          controller.isStale && "opacity-60"
        )}
        aria-busy={controller.isStale || undefined}
      >
        <Table>
          <caption className="sr-only">{label}</caption>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {selection && (
                <TableHead className="w-10">
                  <Checkbox
                    checked={selection.pageSelectionState(pageIds)}
                    onCheckedChange={() => selection.togglePage(pageIds)}
                    aria-label={`Select all ${label.toLowerCase()} on this page`}
                    disabled={pageIds.length === 0}
                  />
                </TableHead>
              )}
              {columns.map((column) => (
                <TableHead
                  key={column.id}
                  // aria-sort belongs on the column header cell, not on the
                  // control inside it.
                  aria-sort={
                    column.sortKey && controller.sort?.key === column.sortKey
                      ? controller.sort.dir === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  className={cn(
                    column.width,
                    column.align && ALIGN[column.align],
                    column.headClassName
                  )}
                >
                  {column.sortKey && controller.sortEnabled ? (
                    <SortableColumnHeader
                      sortKey={column.sortKey}
                      sort={controller.sort}
                      onToggle={controller.toggleSort}
                    >
                      {column.header}
                    </SortableColumnHeader>
                  ) : (
                    column.header
                  )}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody className={cn(rowHref && "[&_tr]:relative")}>{body}</TableBody>
        </Table>
      </div>

      <DataTablePagination controller={controller} />

      {isFiltered && state === "ready" && (
        <p className="text-muted-foreground sr-only" aria-live="polite">
          Filtered results shown.
        </p>
      )}
    </div>
  );
}
