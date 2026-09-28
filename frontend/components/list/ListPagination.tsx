"use client";

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { countLabel, pageWindow, rangeLabel } from "./paging";

/**
 * The two paging modes a list declares (spec 44 §5.1):
 *
 *   numbered  1–25 of 140   ‹ 1 2 3 … 6 ›   25 / page
 *   cursor    Showing 25 · newest first   ‹ Newer  Older ›   25 / page
 *
 * Numbered where an exact count is cheap and the set is stable; cursor where
 * the table is large or rows keep arriving. Pure.
 */

interface PageSizeProps {
  pageSize: number;
  pageSizes: number[];
  onPageSize: (size: number) => void;
}

export type ListPaginationProps = PageSizeProps & { className?: string } & (
    | {
        mode: "numbered";
        /** 1-based. */
        page: number;
        totalCount: number;
        onPage: (page: number) => void;
      }
    | {
        mode: "cursor";
        /** Rows on this page. */
        shown: number;
        /** "newest first", from the list's sort. */
        order?: string;
        hasNewer: boolean;
        hasOlder: boolean;
        onNewer: () => void;
        onOlder: () => void;
        /** Where it is cheap; `approximate` prints "about 1.2k". */
        totalCount?: number | null;
        approximate?: boolean;
      }
  );

export function ListPagination(props: ListPaginationProps) {
  const { pageSize, pageSizes, onPageSize, className } = props;

  return (
    <div
      className={cn(
        "text-muted-foreground flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm",
        className
      )}
    >
      {props.mode === "numbered" ? <Numbered {...props} /> : <Cursor {...props} />}
      <label className="flex items-center gap-2">
        <Select value={String(pageSize)} onValueChange={(v) => onPageSize(Number(v))}>
          <SelectTrigger size="sm" className="w-18 font-mono" aria-label="Rows per page">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((n) => (
              <SelectItem key={n} value={String(n)} className="font-mono">
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span aria-hidden>/ page</span>
      </label>
    </div>
  );
}

function Numbered({
  page,
  pageSize,
  totalCount,
  onPage,
}: Extract<ListPaginationProps, { mode: "numbered" }>) {
  const pageCount = Math.max(1, Math.ceil(totalCount / pageSize));
  const current = Math.min(page, pageCount);
  return (
    <nav aria-label="Pages" className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      <span className="font-mono tabular-nums">{rangeLabel(current, pageSize, totalCount)}</span>
      {pageCount > 1 && (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => onPage(current - 1)}
            disabled={current <= 1}
            aria-label="Previous page"
          >
            <ChevronLeftIcon className="size-4" />
          </Button>
          {pageWindow(current, pageCount).map((item, i) =>
            item === "gap" ? (
              <span key={`gap-${i}`} aria-hidden className="px-1">
                …
              </span>
            ) : (
              <Button
                key={item}
                variant={item === current ? "outline" : "ghost"}
                size="sm"
                className={cn(
                  "min-w-8 font-mono tabular-nums",
                  item === current && "text-foreground"
                )}
                aria-current={item === current ? "page" : undefined}
                aria-label={`Page ${item}`}
                onClick={() => onPage(item)}
              >
                {item}
              </Button>
            )
          )}
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => onPage(current + 1)}
            disabled={current >= pageCount}
            aria-label="Next page"
          >
            <ChevronRightIcon className="size-4" />
          </Button>
        </div>
      )}
    </nav>
  );
}

function Cursor({
  shown,
  order,
  hasNewer,
  hasOlder,
  onNewer,
  onOlder,
  totalCount,
  approximate = false,
}: Extract<ListPaginationProps, { mode: "cursor" }>) {
  return (
    <nav aria-label="Pages" className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      <span className="min-w-0">
        Showing <span className="font-mono tabular-nums">{shown}</span>
        {totalCount != null && (
          <>
            {" "}
            of <span className="font-mono tabular-nums">{countLabel(totalCount, approximate)}</span>
          </>
        )}
        {order && <> · {order}</>}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" onClick={onNewer} disabled={!hasNewer}>
          <ChevronLeftIcon className="size-4" />
          Newer
        </Button>
        <Button variant="ghost" size="sm" onClick={onOlder} disabled={!hasOlder}>
          Older
          <ChevronRightIcon className="size-4" />
        </Button>
      </div>
    </nav>
  );
}
