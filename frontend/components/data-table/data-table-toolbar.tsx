"use client";

import * as React from "react";
import { SearchIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import type { CursorTableController } from "./use-cursor-table";

type DataTableToolbarProps<TRow> = {
  controller: CursorTableController<TRow>;
  searchPlaceholder?: string;
  /** Filter selects, action buttons — rendered beside the search box. */
  children?: React.ReactNode;
  className?: string;
};

export function DataTableToolbar<TRow>({
  controller,
  searchPlaceholder = "Search…",
  children,
  className,
}: DataTableToolbarProps<TRow>) {
  const { search, setSearch, searchEnabled, totalCount } = controller;

  if (!searchEnabled && !children && totalCount === null) return null;

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {searchEnabled && (
        <div className="relative min-w-56 flex-1 sm:max-w-xs">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={searchPlaceholder}
            className="pl-8"
            aria-label={searchPlaceholder}
          />
          {search && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSearch("")}
              className="absolute top-1/2 right-1 size-6 -translate-y-1/2"
              aria-label="Clear search"
            >
              <XIcon className="size-3.5" />
            </Button>
          )}
        </div>
      )}
      {children}
      {totalCount !== null && (
        <span className="text-muted-foreground ml-auto text-sm tabular-nums">
          {totalCount === 1 ? "1 result" : `${totalCount.toLocaleString()} results`}
        </span>
      )}
    </div>
  );
}
