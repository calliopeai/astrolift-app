"use client";

import { useTranslations } from "next-intl";

import * as React from "react";
import { ArrowDownIcon, ArrowUpIcon, ChevronsUpDownIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import type { SortState } from "./types";

type SortableColumnHeaderProps = {
  sortKey: string;
  /** One key, or the ordered keys of a multi-key sort (spec 44 §5.1). */
  sort: SortState | SortState[] | undefined;
  /** `additive` is true on shift-click: add this column as the next key. */
  onToggle: (key: string, additive: boolean) => void;
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
  const t = useTranslations("shared.table");
  const keys = Array.isArray(sort) ? sort : sort ? [sort] : [];
  const index = keys.findIndex((s) => s.key === sortKey);
  const active = index !== -1;
  const dir = active ? keys[index].dir : undefined;

  return (
    <button
      type="button"
      onClick={(e) => onToggle(sortKey, e.shiftKey)}
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
      {active && keys.length > 1 && (
        <span className="text-muted-foreground text-2xs font-mono tabular-nums">
          {t.rich("sortKey", {
            index: index + 1,
            order: (chunks) => <span className="sr-only">{chunks}</span>,
          })}
        </span>
      )}
    </button>
  );
}
