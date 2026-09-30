"use client";

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import * as React from "react";
import { useFormatter, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { pageWindow } from "./paging";

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
  const t = useTranslations("shared.pagination");
  const fmt = useFormatter();
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
          <SelectTrigger size="sm" className="w-18 font-mono" aria-label={t("rowsPerPage")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((n) => (
              <SelectItem key={n} value={String(n)} className="font-mono">
                {fmt.number(n)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span aria-hidden>{t("perPage")}</span>
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
  const t = useTranslations("shared.pagination");
  const fmt = useFormatter();
  const pageCount = Math.max(1, Math.ceil(totalCount / pageSize));
  const current = Math.min(page, pageCount);
  return (
    <nav aria-label={t("pages")} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      <span className="font-mono tabular-nums">
        {totalCount === 0
          ? t("emptyRange")
          : t("range", {
              start: (current - 1) * pageSize + 1,
              end: Math.min(current * pageSize, totalCount),
              total: totalCount,
            })}
      </span>
      {pageCount > 1 && (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => onPage(current - 1)}
            disabled={current <= 1}
            aria-label={t("previousPage")}
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
                aria-label={t("page", { page: item })}
                onClick={() => onPage(item)}
              >
                {fmt.number(item)}
              </Button>
            )
          )}
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => onPage(current + 1)}
            disabled={current >= pageCount}
            aria-label={t("nextPage")}
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
  const t = useTranslations("shared.pagination");
  const fmt = useFormatter();
  const total =
    totalCount == null
      ? null
      : approximate
        ? t("approximately", {
            count: fmt.number(totalCount, { notation: "compact", maximumFractionDigits: 1 }),
          })
        : fmt.number(totalCount);
  const numeric = (chunks: React.ReactNode) => (
    <span className="font-mono tabular-nums">{chunks}</span>
  );
  return (
    <nav aria-label={t("pages")} className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
      <span className="min-w-0">
        {total == null
          ? t.rich("showing", { shown, shownCount: numeric })
          : t.rich("showingOf", { shown, total, shownCount: numeric, totalCount: numeric })}
        {order && <> · {order}</>}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" onClick={onNewer} disabled={!hasNewer}>
          <ChevronLeftIcon className="size-4" />
          {t("newer")}
        </Button>
        <Button variant="ghost" size="sm" onClick={onOlder} disabled={!hasOlder}>
          {t("older")}
          <ChevronRightIcon className="size-4" />
        </Button>
      </div>
    </nav>
  );
}
