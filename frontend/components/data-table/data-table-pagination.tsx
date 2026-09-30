"use client";

import { useTranslations } from "next-intl";

import * as React from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { PAGE_SIZES, type CursorTableController } from "./use-cursor-table";

type DataTablePaginationProps<TRow> = {
  controller: CursorTableController<TRow>;
  className?: string;
};

/**
 * Prev / next only, no page-number jump.
 *
 * A keyset walk has no "page 7" to jump to: cursors are positions, not
 * offsets, so the only reachable pages are the ones adjacent to a cursor
 * already held. The old server table accepted an `onGoToPage` prop and
 * silently never called it; being unable to render the control is more
 * honest than rendering one that lies.
 */
export function DataTablePagination<TRow>({
  controller,
  className,
}: DataTablePaginationProps<TRow>) {
  const t = useTranslations("shared.pagination");
  const { pageIndex, hasNext, hasPrev, next, prev, pageSize, setPageSize } = controller;

  if (!hasNext && !hasPrev) return null;

  return (
    <div className={cn("flex flex-wrap items-center justify-end gap-4", className)}>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm">{t("rowsPerPage")}</span>
        <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
          <SelectTrigger size="sm" className="w-18" aria-label={t("rowsPerPage")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZES.map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-sm tabular-nums">
          {t("page", { page: pageIndex + 1 })}
        </span>
        <Button
          variant="outline"
          size="icon"
          onClick={prev}
          disabled={!hasPrev}
          aria-label={t("previousPage")}
        >
          <ChevronLeftIcon className="size-4" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          onClick={next}
          disabled={!hasNext}
          aria-label={t("nextPage")}
        >
          <ChevronRightIcon className="size-4" />
        </Button>
      </div>
    </div>
  );
}
