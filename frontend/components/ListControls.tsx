"use client";

/**
 * ListControls — reusable search + page-size + pagination bar.
 *
 * Pairs with useListControls(). Drop it above the list, pass the result
 * from the hook as props, and it renders:
 *   [Search input]  ........  [Page size selector]  [X of Y]  [← 1 / N →]
 *
 * For table column headers that need sort arrows, use SortableHeader.
 */

import * as React from "react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  ChevronsUpDownIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ListControlsResult } from "@/hooks/use-list-controls";

// ---------------------------------------------------------------------------
// ListControls bar
// ---------------------------------------------------------------------------

interface ListControlsProps<T> {
  controls: ListControlsResult<T>;
  searchPlaceholder?: string;
  /** Hide the search input (e.g. when parent already has its own filter). */
  hideSearch?: boolean;
  className?: string;
}

export function ListControls<T>({
  controls,
  searchPlaceholder = "Search…",
  hideSearch = false,
  className,
}: ListControlsProps<T>) {
  const {
    query,
    setQuery,
    pageIndex,
    setPageIndex,
    pageSize,
    setPageSize,
    pageSizes,
    totalFiltered,
    pageCount,
  } = controls;

  const start = totalFiltered === 0 ? 0 : pageIndex * pageSize + 1;
  const end = Math.min((pageIndex + 1) * pageSize, totalFiltered);

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className ?? ""}`}>
      {/* Search */}
      {!hideSearch && (
        <div className="relative flex-1 min-w-[180px] max-w-xs">
          <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            className="pl-8 h-8 text-sm"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <XIcon className="size-3.5" />
            </button>
          )}
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Page size */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground whitespace-nowrap">Per page</span>
        <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
          <SelectTrigger className="h-8 w-16 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((s) => (
              <SelectItem key={s} value={String(s)} className="text-xs">
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Count */}
      {totalFiltered > 0 && (
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {start}–{end} of {totalFiltered}
        </span>
      )}

      {/* Pagination */}
      {pageCount > 1 && (
        <div className="flex items-center gap-0.5">
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageIndex(0)}
            disabled={pageIndex === 0}
          >
            <ChevronsLeftIcon className="size-3.5" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageIndex(pageIndex - 1)}
            disabled={pageIndex === 0}
          >
            <ChevronLeftIcon className="size-3.5" />
          </Button>
          <span className="text-xs text-muted-foreground px-1 whitespace-nowrap">
            {pageIndex + 1} / {pageCount}
          </span>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageIndex(pageIndex + 1)}
            disabled={pageIndex >= pageCount - 1}
          >
            <ChevronRightIcon className="size-3.5" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            onClick={() => setPageIndex(pageCount - 1)}
            disabled={pageIndex >= pageCount - 1}
          >
            <ChevronsRightIcon className="size-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SortableHeader — drop-in replacement for <TableHead> text in table views
// ---------------------------------------------------------------------------

interface SortableHeaderProps {
  sortKey: string;
  sort: { key: string; dir: "asc" | "desc" } | undefined;
  onToggle: (key: string) => void;
  children: React.ReactNode;
  className?: string;
}

export function SortableHeader({
  sortKey,
  sort,
  onToggle,
  children,
  className,
}: SortableHeaderProps) {
  const active = sort?.key === sortKey;
  const dir = active ? sort?.dir : undefined;

  return (
    <button
      onClick={() => onToggle(sortKey)}
      className={`flex items-center gap-1 text-sm font-medium hover:text-foreground transition-colors ${
        active ? "text-foreground" : "text-muted-foreground"
      } ${className ?? ""}`}
    >
      {children}
      {dir === "asc" ? (
        <ArrowUpIcon className="size-3.5" />
      ) : dir === "desc" ? (
        <ArrowDownIcon className="size-3.5" />
      ) : (
        <ChevronsUpDownIcon className="size-3.5 opacity-40" />
      )}
    </button>
  );
}
