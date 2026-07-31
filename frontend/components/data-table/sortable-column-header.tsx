"use client";

import * as React from "react";
import { ArrowDownIcon, ArrowUpIcon, ChevronsUpDownIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import type { SortState } from "./types";

type SortableColumnHeaderProps = {
  sortKey: string;
  sort: SortState | undefined;
  onToggle: (key: string) => void;
  children: React.ReactNode;
  className?: string;
};

/**
 * The sort control, reachable only through a `Column.sortKey`.
 *
 * Its predecessor, `SortableHeader`, was exported for anyone to drop
 * anywhere, and five surfaces rendered it detached from any table — as
 * floating buttons above a card grid or a `<ul>`, where nothing tied
 * "Name" to a column. Keeping this one internal to the DataTable and
 * driving it from the column def makes that placement unexpressible.
 */
export function SortableColumnHeader({
  sortKey,
  sort,
  onToggle,
  children,
  className,
}: SortableColumnHeaderProps) {
  const active = sort?.key === sortKey;
  const dir = active ? sort?.dir : undefined;

  return (
    <button
      type="button"
      onClick={() => onToggle(sortKey)}
      className={cn(
        "hover:text-foreground flex items-center gap-1 text-sm font-medium transition-colors",
        active ? "text-foreground" : "text-muted-foreground",
        className
      )}
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
