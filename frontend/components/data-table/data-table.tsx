"use client";

import { useLocale, useTranslations } from "next-intl";

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
import type { Column, EmptyStateSpec, SortState } from "./types";
import type { CursorTableController } from "./use-cursor-table";
import type { RowSelection } from "./use-row-selection";

type RowActivation<TRow> =
  | { rowHref: (row: TRow) => string; onRowActivate?: never; rowLabel?: never }
  | {
      onRowActivate: (row: TRow) => void;
      /**
       * Names this row's activator. It is the button's accessible name,
       * so it is all a screen-reader user hears before deciding to press
       * it, and it must contain the row's visible first-cell text.
       *
       * Identify the row rather than describe the gesture: the element is
       * a button and its role already supplies the verb. Building the
       * name out of the row's own fields also keeps it out of the message
       * catalogues, where a new key means eight locales.
       *
       * Required rather than optional because a stretched activator with
       * no name reads as an unlabelled button on every row, which is
       * worse than the mouse-only row it replaces.
       */
      rowLabel: (row: TRow) => string;
      rowHref?: never;
    }
  | { rowHref?: never; onRowActivate?: never; rowLabel?: never };

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
  /**
   * `"table"` renders the table alone, without the toolbar and pagination:
   * for `ListPage`, which draws the shared FilterBar and ListPagination
   * around it (spec 44 §5.1). Default `"full"`.
   */
  chrome?: "full" | "table";
  /**
   * A multi-key sort, overriding the controller's single key: the ordered
   * keys, and a toggle that is told whether the click was a shift-click.
   */
  sorts?: SortState[];
  onSortToggle?: (key: string, additive: boolean) => void;
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
  rowLabel,
  rowClassName,
  chrome = "full",
  sorts,
  onSortToggle,
  className,
}: DataTableProps<TRow>) {
  const t = useTranslations("shared.table");
  const locale = useLocale();
  const noun = locale.startsWith("en") ? label.toLocaleLowerCase(locale) : label;
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
              <div role="alert" className="flex flex-col items-center gap-3 text-center">
                <AlertTriangleIcon className="text-danger size-5" />
                <div>
                  <p className="font-medium">{t("loadFailed", { label: noun })}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {error?.message ?? t("requestFailed")}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={retry}>
                  {t("retry")}
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
                  <p className="font-medium">{emptyFiltered?.title ?? t("noMatches")}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {emptyFiltered?.description ?? t("noMatchesDescription", { label: noun })}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  {t("clearSearch")}
                </Button>
              </div>
            </TableCell>
          </TableRow>
        );

      case "empty":
        return (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={columnCount} className="p-0">
              <div className="sticky left-0 w-[100cqw] max-w-full">
                <EmptyState {...empty} />
              </div>
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
              // `relative`: the row's activator below is stretched with
              // `after:inset-0`, which reaches exactly as far as the nearest
              // positioned ancestor. Without it the row is not one and
              // `table-container` is, so every row's overlay covers the whole
              // table and the last row in the DOM swallows every click
              // (#1502).
              className={cn(
                (rowHref || onRowActivate) && "relative cursor-pointer",
                rowClassName?.(row)
              )}
            >
              {selection && (
                // relative z-10: above the row's stretched link, so the box
                // selects the row instead of opening it.
                <TableCell className="relative z-10 w-10" onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selected}
                    onCheckedChange={() => selection.toggle(id)}
                    aria-label={t("selectRow", { id })}
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
                  {/* One control per row, stretched over the row, so the
                      whole row is activatable without nesting a control per
                      cell — and so it is reachable by keyboard, which a
                      handler on the <tr> never was (#1503). */}
                  {column.id === columns[0].id && rowHref ? (
                    <Link href={rowHref(row)} className="after:absolute after:inset-0">
                      {column.cell(row)}
                    </Link>
                  ) : column.id === columns[0].id && onRowActivate ? (
                    <button
                      type="button"
                      // Optional at runtime although the prop types require
                      // it: an unlabelled button still falls back to the
                      // cell's own text, and a reachable row with a thin
                      // name beats a row no keyboard can reach.
                      aria-label={rowLabel?.(row)}
                      onClick={() => onRowActivate(row)}
                      className="text-left after:absolute after:inset-0"
                    >
                      {column.cell(row)}
                    </button>
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
      {chrome === "full" && (
        <DataTableToolbar controller={controller} searchPlaceholder={searchPlaceholder}>
          {toolbar}
        </DataTableToolbar>
      )}

      {selection && selection.selectedCount > 0 && bulkActions && (
        <div className="bg-muted/50 flex flex-wrap items-center gap-3 rounded-md border px-3 py-2">
          <span className="text-sm font-medium tabular-nums">
            {t("selected", { count: selection.selectedCount })}
          </span>
          <Button variant="ghost" size="sm" onClick={selection.clear}>
            {t("clear")}
          </Button>
          <div className="ml-auto flex flex-wrap items-center gap-2">{bulkActions(selection)}</div>
        </div>
      )}

      {/* Rows persist across a refetch rather than blanking, so fade them
          while they are answering the previous question. */}
      <div
        className={cn("rounded-md border transition-opacity", controller.isStale && "opacity-60")}
        aria-busy={state === "loading" || controller.isStale || undefined}
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
                    aria-label={t("selectPage", { label: noun })}
                    disabled={pageIds.length === 0}
                  />
                </TableHead>
              )}
              {columns.map((column) => (
                <TableHead
                  key={column.id}
                  // aria-sort belongs on the column header cell, not on the
                  // control inside it.
                  aria-sort={ariaSort(column.sortKey, sorts ?? controller.sort)}
                  className={cn(
                    column.width,
                    column.align && ALIGN[column.align],
                    column.headClassName
                  )}
                >
                  {column.sortKey && (onSortToggle || controller.sortEnabled) ? (
                    <SortableColumnHeader
                      sortKey={column.sortKey}
                      sort={sorts ?? controller.sort}
                      onToggle={onSortToggle ?? ((key) => controller.toggleSort(key))}
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

      {chrome === "full" && <DataTablePagination controller={controller} />}

      {state === "loading" && (
        <p className="sr-only" role="status">
          {t("loading")}
        </p>
      )}

      {isFiltered && state === "ready" && (
        <p className="text-muted-foreground sr-only" aria-live="polite">
          {t("filteredResults")}
        </p>
      )}
    </div>
  );
}

/** aria-sort for a header: only the primary key is announced as sorted. */
function ariaSort(
  sortKey: string | undefined,
  sort: SortState | SortState[] | undefined
): "ascending" | "descending" | undefined {
  const primary = Array.isArray(sort) ? sort[0] : sort;
  if (!sortKey || primary?.key !== sortKey) return undefined;
  return primary.dir === "asc" ? "ascending" : "descending";
}
