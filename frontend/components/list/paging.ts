/** Pure helpers behind ListPagination, unit-tested in use-list-state.test.ts. */

export type PageItem = number | "gap";

/**
 * The page buttons for `‹ 1 2 3 … 6 ›`: always the first and last page, the
 * current page with one either side, and a gap wherever pages are skipped.
 * A gap that would hide a single page shows the page instead.
 */
export function pageWindow(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 1) return [1];
  const keep = new Set([1, pageCount, page - 1, page, page + 1]);
  const items: PageItem[] = [];
  let last = 0;
  for (let n = 1; n <= pageCount; n++) {
    if (!keep.has(n)) continue;
    if (n - last === 2) items.push(n - 1);
    else if (n - last > 2) items.push("gap");
    items.push(n);
    last = n;
  }
  return items;
}

/** "1–25 of 140". */
export function rangeLabel(page: number, pageSize: number, total: number): string {
  if (total === 0) return "0 of 0";
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return `${start.toLocaleString("en-US")}–${end.toLocaleString("en-US")} of ${total.toLocaleString("en-US")}`;
}

/** "about 1.2k" for an approximate count; exact counts print in full. */
export function countLabel(count: number, approximate: boolean): string {
  if (!approximate) return count.toLocaleString("en-US");
  if (count < 1000) return `about ${count}`;
  const k = count / 1000;
  if (k < 1000) return `about ${k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)}k`;
  const m = count / 1_000_000;
  return `about ${m < 10 ? m.toFixed(1).replace(/\.0$/, "") : Math.round(m)}m`;
}
