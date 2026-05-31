"use client";

import { ChevronDownIcon, ChevronUpIcon, ChevronsUpDownIcon } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ListControlsResult, SortState } from "@/hooks/use-list-controls";

// ─── ListControls ─────────────────────────────────────────────────────────────

interface ListControlsProps<T> {
  controls: ListControlsResult<T>;
  searchPlaceholder?: string;
  className?: string;
}

export function ListControls<T>({
  controls,
  searchPlaceholder = "Filter...",
  className,
}: ListControlsProps<T>) {
  const {
    query,
    setQuery,
    totalFiltered,
    pageIndex,
    setPageIndex,
    pageCount,
    pageSize,
    setPageSize,
    pageSizes,
  } = controls;

  return (
    <div className={cn("flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-3", className)}>
      <Input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={searchPlaceholder}
        className="h-8 w-full sm:w-64 text-sm"
      />

      <div className="flex items-center gap-2 text-sm text-muted-foreground shrink-0">
        <span className="hidden sm:inline">
          {totalFiltered === 0
            ? "No results"
            : `${pageIndex * pageSize + 1}–${Math.min((pageIndex + 1) * pageSize, totalFiltered)} of ${totalFiltered}`}
        </span>

        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-7"
            disabled={pageIndex === 0}
            onClick={() => setPageIndex(pageIndex - 1)}
            aria-label="Previous page"
          >
            <ChevronLeftIcon className="size-3.5" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-7"
            disabled={pageIndex >= pageCount - 1}
            onClick={() => setPageIndex(pageIndex + 1)}
            aria-label="Next page"
          >
            <ChevronRightIcon className="size-3.5" />
          </Button>
        </div>

        <select
          value={pageSize}
          onChange={(e) => setPageSize(Number(e.target.value))}
          className="h-7 rounded-md border border-input bg-background px-2 text-xs"
          aria-label="Rows per page"
        >
          {pageSizes.map((s) => (
            <option key={s} value={s}>
              {s} / page
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

// ─── SortableHeader ───────────────────────────────────────────────────────────

interface SortableHeaderProps {
  sortKey: string;
  sort: SortState;
  onToggle: (key: string) => void;
  children: React.ReactNode;
  className?: string;
}

export function SortableHeader({ sortKey, sort, onToggle, children, className }: SortableHeaderProps) {
  const isActive = sort.key === sortKey;
  const direction = isActive ? sort.direction : null;

  return (
    <button
      type="button"
      onClick={() => onToggle(sortKey)}
      className={cn(
        "flex items-center gap-1 text-left font-medium hover:text-foreground transition-colors",
        isActive ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      {children}
      {direction === "asc" ? (
        <ChevronUpIcon className="size-3.5 shrink-0" />
      ) : direction === "desc" ? (
        <ChevronDownIcon className="size-3.5 shrink-0" />
      ) : (
        <ChevronsUpDownIcon className="size-3.5 shrink-0 opacity-40" />
      )}
    </button>
  );
}

// ─── internal icon shims ───────────────────────────────────────────────────────

function ChevronLeftIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
