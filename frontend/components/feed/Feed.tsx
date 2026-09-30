"use client";

import { useFormatter, useLocale, useNow, useTimeZone, useTranslations } from "next-intl";

/**
 * Feed: the one primitive for things that grow (Leo's list rule 5, spec 44
 * §5.1 "feeds that are read rather than acted on"): activity, events,
 * timelines, audit, permission and role listings. Logs keep LogView.
 *
 *   const activity = useRecentActivity();
 *   <Feed
 *     label="Activity"
 *     items={activity.items}
 *     keyOf={(i) => i.id}
 *     renderItem={(i) => <ActivityRow item={i} />}
 *     groupBy={{ day: (i) => i.occurredAt }}
 *     loading={activity.loading}
 *     error={activity.error}
 *     onRetry={activity.refetch}
 *     hasMore={activity.hasMore}
 *     loadingMore={activity.loadingMore}
 *     onLoadMore={activity.onLoadMore}
 *     newCount={held.newCount}
 *     onShowNew={held.reveal}
 *   />
 *
 * It scrolls inside its own frame (`maxHeight`, default one panel's worth),
 * so the page never grows with it. Near the end (one frame height away) it
 * asks for the next page on the caller's cursor, at most once per page; a
 * Load older button stays at the end as the fallback, which is also how a
 * failed page is retried. New items wait behind the "n new" pill
 * (`useHeldRows`), and if a caller prepends anyway the frame holds the
 * reader's place. Groups (a day, or a caller key such as a run's round) get
 * a sticky heading. Pure: the data and the cursor come from a hook.
 */

import { AlertTriangleIcon, InboxIcon } from "lucide-react";
import * as React from "react";

import type { EmptyStateSpec } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { NewRowsPill } from "@/components/list/NewRowsPill";
import { SkeletonRows } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { type FeedGroupBy, groupItems, useHoldPlace, useLoadMore, useNearEnd } from "./use-feed";

export type { FeedGroupBy };

export interface FeedProps<T> {
  /** Names the frame for assistive tech and the empty and error copy ("Activity"). */
  label: string;
  items: T[];
  renderItem: (item: T) => React.ReactNode;
  keyOf: (item: T) => string;
  /** `{ day: (i) => i.at }` or `{ key: (i) => i.round, label: (k) => \`Round ${k}\` }`. */
  groupBy?: FeedGroupBy<T>;
  /** First fetch in flight. Skeleton rows while there is nothing to show. */
  loading?: boolean;
  /** With no items it takes the frame; with items it sits at the end. */
  error?: string | { message: string } | null;
  onRetry?: () => void;
  empty?: EmptyStateSpec;
  /** The cursor has another page. */
  hasMore?: boolean;
  loadingMore?: boolean;
  /** Fetch the next (older) page on the cursor. */
  onLoadMore?: () => void;
  /** Items that arrived above the ones being read (live feeds). */
  newCount?: number;
  onShowNew?: () => void;
  /** Height class for the frame. Defaults to `max-h-96`, which fits a panel. */
  maxHeight?: string;
  /** Tighter rows, for side panels and sheets. */
  dense?: boolean;
  /** Heading of the error state. Defaults to "Could not load <label>". */
  errorTitle?: string;
  /** Button copy, for a translated caller. */
  loadOlderLabel?: string;
  loadingOlderLabel?: string;
  className?: string;
}

export function Feed<T>({
  label,
  items,
  renderItem,
  keyOf,
  groupBy,
  loading = false,
  error,
  onRetry,
  empty,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  newCount = 0,
  onShowNew,
  maxHeight = "max-h-96",
  dense = false,
  errorTitle,
  loadOlderLabel,
  loadingOlderLabel,
  className,
}: FeedProps<T>) {
  const t = useTranslations("shared.feed");
  const locale = useLocale();
  const timeZone = useTimeZone() ?? "UTC";
  const now = useNow({ updateInterval: 60_000 });
  const fmt = useFormatter();
  const noun = locale.startsWith("en") ? label.toLocaleLowerCase(locale) : label;
  const frame = React.useRef<HTMLDivElement | null>(null);
  const sentinel = React.useRef<HTMLDivElement | null>(null);
  const errorMessage = typeof error === "string" ? error : (error?.message ?? null);

  const loader = useLoadMore({ hasMore, loadingMore, itemCount: items.length }, onLoadMore);
  useNearEnd(frame, sentinel, loader.auto, hasMore && items.length > 0, items.length);
  const hold = useHoldPlace(items.length > 0 ? keyOf(items[0]!) : null);
  React.useLayoutEffect(() => {
    if (frame.current) hold(frame.current);
  });

  const groups = groupItems(items, groupBy, {
    timeZone,
    now,
    today: t("today"),
    yesterday: t("yesterday"),
    unknownDate: t("unknownDate"),
    formatDate: (date) =>
      fmt.dateTime(date, { year: "numeric", month: "short", day: "numeric", timeZone }),
  });

  const showNew = () => {
    onShowNew?.();
    if (frame.current) frame.current.scrollTop = 0;
  };

  if (items.length === 0) {
    return (
      <div className={cn("min-w-0", className)} aria-busy={loading || undefined}>
        {loading ? (
          <>
            <p className="sr-only" role="status">
              {t("loading")}
            </p>
            <SkeletonRows />
          </>
        ) : errorMessage ? (
          <FeedError
            title={errorTitle ?? t("loadFailed", { label: noun })}
            message={errorMessage}
            onRetry={onRetry}
          />
        ) : (
          <EmptyState
            {...(empty ?? {
              icon: <InboxIcon className="size-5" />,
              title: t("empty", { label: noun }),
            })}
          />
        )}
      </div>
    );
  }

  return (
    <div className={cn("relative min-w-0", className)}>
      {onShowNew && (
        // Floats over the frame's top edge, so it never pushes the rows.
        <div className="pointer-events-none absolute inset-x-0 top-2 z-20">
          <NewRowsPill count={newCount} onReveal={showNew} className="pointer-events-auto" />
        </div>
      )}
      <div
        ref={frame}
        role="region"
        aria-label={label}
        aria-busy={loadingMore || undefined}
        tabIndex={0}
        className={cn(
          "focus-visible:ring-ring min-w-0 overflow-y-auto overscroll-contain focus-visible:ring-2 focus-visible:outline-none",
          maxHeight
        )}
      >
        {groups.map((group) => (
          <section key={group.key} className="min-w-0">
            {group.label !== null && (
              <h3 className="bg-card text-muted-foreground sticky top-0 z-10 border-b px-1 py-1.5 text-xs font-medium [overflow-wrap:anywhere]">
                {group.label}
              </h3>
            )}
            <ul className="min-w-0 divide-y">
              {group.items.map((item) => (
                <li key={keyOf(item)} className={cn("min-w-0", dense ? "py-1.5" : "py-3")}>
                  {renderItem(item)}
                </li>
              ))}
            </ul>
          </section>
        ))}
        <div ref={sentinel} aria-hidden className="h-px" />
        <div className="flex min-w-0 flex-col items-center gap-2 border-t py-3">
          {errorMessage && (
            <p
              role="alert"
              className="text-danger-fg flex min-w-0 items-start gap-1.5 text-xs [overflow-wrap:anywhere]"
            >
              <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span className="min-w-0 font-mono">{errorMessage}</span>
            </p>
          )}
          {hasMore ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={loader.manual}
              disabled={loadingMore}
            >
              {loadingMore
                ? (loadingOlderLabel ?? t("loading"))
                : (loadOlderLabel ?? t("loadOlder"))}
            </Button>
          ) : errorMessage && onRetry ? (
            <Button type="button" variant="ghost" size="sm" onClick={onRetry}>
              {t("retry")}
            </Button>
          ) : (
            <p className="text-muted-foreground text-xs">{t("noOlderItems")}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function FeedError({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  const t = useTranslations("shared.feed");
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-6 text-center">
      <AlertTriangleIcon className="text-danger size-5" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
          {message}
        </p>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          {t("retry")}
        </Button>
      )}
    </div>
  );
}
