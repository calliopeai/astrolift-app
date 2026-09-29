"use client";

import * as React from "react";

/**
 * The behaviour half of Feed (Leo's list rule 5): lazy-load older items on a
 * cursor as the reader nears the end, grouping, and holding the reader's
 * place when items arrive above them.
 */

export interface LoadMoreState {
  hasMore: boolean;
  loadingMore: boolean;
  /** Items on screen; a new page changes it. */
  itemCount: number;
}

/**
 * Whether the sentinel may ask for the next page. It asks at most once per
 * page: after a request at `itemCount` n it waits until the count moves, so a
 * sentinel that stays in view (a short page, a failed fetch) never loops. The
 * Load older button is the way past a failed fetch.
 */
export function mayAutoLoad(state: LoadMoreState, requestedAt: number | null): boolean {
  if (!state.hasMore || state.loadingMore) return false;
  return requestedAt !== state.itemCount;
}

export interface LoadMoreController {
  /** Called by the sentinel. Guarded: see `mayAutoLoad`. */
  auto: () => void;
  /** Called by the Load older button. Only guarded against a fetch in flight. */
  manual: () => void;
}

export function useLoadMore(
  state: LoadMoreState,
  onLoadMore: (() => void) | undefined
): LoadMoreController {
  const requestedAt = React.useRef<number | null>(null);
  const latest = React.useRef({ state, onLoadMore });
  React.useLayoutEffect(() => {
    latest.current = { state, onLoadMore };
  });

  return React.useMemo(
    () => ({
      auto: () => {
        const { state: s, onLoadMore: load } = latest.current;
        if (!load || !mayAutoLoad(s, requestedAt.current)) return;
        requestedAt.current = s.itemCount;
        load();
      },
      manual: () => {
        const { state: s, onLoadMore: load } = latest.current;
        if (!load || !s.hasMore || s.loadingMore) return;
        requestedAt.current = s.itemCount;
        load();
      },
    }),
    []
  );
}

/**
 * Watch a sentinel at the end of a scroll frame and call `onNear` when it
 * comes within one frame height of view. Without IntersectionObserver
 * (jsdom, very old browsers) it does nothing and the button carries the feed.
 * It re-observes when `itemCount` changes: an observer only reports a change
 * of intersection, so a page too short to push the sentinel out of range
 * would otherwise never ask again.
 */
export function useNearEnd(
  frame: React.RefObject<HTMLElement | null>,
  sentinel: React.RefObject<HTMLElement | null>,
  onNear: () => void,
  enabled: boolean,
  itemCount: number
) {
  React.useEffect(() => {
    const root = frame.current;
    const target = sentinel.current;
    if (!enabled || !root || !target || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onNear();
      },
      // Percentages resolve against the root: one frame height below it.
      { root, rootMargin: "0px 0px 100% 0px" }
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [frame, sentinel, onNear, enabled, itemCount]);
}

/**
 * Keep the reader's place when items are added above them. A live feed
 * should hold new items behind the pill (`useHeldRows`); this covers a
 * caller that prepends anyway: if the first item changed while the reader is
 * scrolled down, the frame moves by the height that was added. The caller
 * runs `hold` in a layout effect after every render:
 *
 *   const hold = useHoldPlace(firstKey);
 *   React.useLayoutEffect(() => { if (frame.current) hold(frame.current); });
 */
export function useHoldPlace(firstKey: string | null): (frame: HTMLElement) => void {
  const previous = React.useRef<{ key: string | null; height: number }>({ key: null, height: 0 });
  return React.useCallback(
    (frame: HTMLElement) => {
      const prev = previous.current;
      if (prev.key !== null && firstKey !== prev.key && frame.scrollTop > 0) {
        frame.scrollTop += frame.scrollHeight - prev.height;
      }
      previous.current = { key: firstKey, height: frame.scrollHeight };
    },
    [firstKey]
  );
}

/** How a feed groups its items: by local day of a timestamp, or by a caller's key. */
export type FeedGroupBy<T> =
  | { day: (item: T) => string | number | Date }
  | { key: (item: T) => string; label?: (key: string, first: T) => React.ReactNode };

export interface FeedGroup<T> {
  /** Unique per group: the group key and its position. */
  key: string;
  label: React.ReactNode;
  items: T[];
}

function dayKey(at: string | number | Date): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "unknown";
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** "Today", "Yesterday", or the date, in the reader's locale and time zone. */
export function dayLabel(at: string | number | Date, now: Date = new Date()): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "Unknown date";
  const key = dayKey(d);
  if (key === dayKey(now)) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === dayKey(yesterday)) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
  }).format(d);
}

/**
 * Consecutive runs of the same key, in feed order. A feed is ordered, so a
 * key that comes back later (a run's round 2 after round 3) starts a new
 * group rather than pulling items out of order.
 */
export function groupItems<T>(items: T[], groupBy: FeedGroupBy<T> | undefined): FeedGroup<T>[] {
  if (!groupBy) return [{ key: "all", label: null, items }];
  const groups: FeedGroup<T>[] = [];
  let lastKey: string | null = null;
  for (const item of items) {
    const key = "day" in groupBy ? dayKey(groupBy.day(item)) : groupBy.key(item);
    const last = groups[groups.length - 1];
    if (last && lastKey === key) {
      last.items.push(item);
      continue;
    }
    lastKey = key;
    const label =
      "day" in groupBy ? dayLabel(groupBy.day(item)) : (groupBy.label?.(key, item) ?? key);
    groups.push({ key: `${key}#${groups.length}`, label, items: [item] });
  }
  return groups;
}
