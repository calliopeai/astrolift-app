"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  BoxIcon,
  CloudCogIcon,
  KeyRoundIcon,
  RocketIcon,
  ScalingIcon,
  ServerCogIcon,
  SettingsIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_RECENT_ACTIVITY } from "@/graphql/operations/operations.queries";
import type {
  AstroliftActivityItem,
  AstroliftActivityPage,
} from "@/graphql/operations/operations.types";

interface RecentActivityResp {
  astroliftRecentActivity: AstroliftActivityPage;
}

interface ActivityFeedProps {
  /** Items per page when first fetching / when loading the next page. */
  pageSize?: number;
}

/**
 * Dashboard "Activity" card (#435 scope A).
 *
 * Replaces the placeholder card that previously read "Cluster-wide
 * event stream (coming soon)". Streams the live lifecycle event log
 * from ``astroliftRecentActivity`` (cursor-paginated) and renders
 * each row with an icon, actor, action verb, target label, and a
 * relative timestamp. Rows with a backend-resolved ``targetHref``
 * become drill-down links into the source resource — deploy →
 * ``/apps/<slug>/deployments/<id>``, cluster → ``/clusters/<id>``,
 * secret rotation → ``/apps/<slug>/secrets``, etc.
 *
 * "Load more" uses ``fetchMore`` so we don't re-fetch the entire
 * history when the operator wants the next page — the cursor encodes
 * ``(occurred_at, guid)`` over the underlying ``Event`` table.
 */
export function ActivityFeed({ pageSize = 10 }: ActivityFeedProps) {
  const t = useTranslations("overview.activity");
  const { data, loading, error, fetchMore } = useQuery<RecentActivityResp>(GET_RECENT_ACTIVITY, {
    variables: { limit: pageSize },
    fetchPolicy: "cache-and-network",
  });

  const page = data?.astroliftRecentActivity;
  const items = page?.items ?? [];
  const nextCursor = page?.nextCursor ?? null;
  const [loadingMore, setLoadingMore] = React.useState(false);

  const handleLoadMore = React.useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      await fetchMore({
        variables: { limit: pageSize, cursor: nextCursor },
        // Merge page-by-page so the rendered list grows; nextCursor
        // always reflects the *latest* fetch so a third click works.
        updateQuery: (prev, { fetchMoreResult }) => {
          if (!fetchMoreResult) return prev;
          return {
            astroliftRecentActivity: {
              ...fetchMoreResult.astroliftRecentActivity,
              items: [
                ...prev.astroliftRecentActivity.items,
                ...fetchMoreResult.astroliftRecentActivity.items,
              ],
            },
          };
        },
      });
    } finally {
      setLoadingMore(false);
    }
  }, [fetchMore, loadingMore, nextCursor, pageSize]);

  if (loading && items.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (error && items.length === 0) {
    return (
      <EmptyState
        icon={<ActivityIcon className="size-5" />}
        title={t("errorTitle")}
        description={error.message}
      />
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<ActivityIcon className="size-5" />}
        title={t("emptyTitle")}
        description={t("emptyDescription")}
      />
    );
  }

  return (
    <div>
      <ul className="divide-y">
        {items.map((item) => (
          <ActivityRow key={item.id} item={item} />
        ))}
      </ul>
      {nextCursor && (
        <div className="mt-3 flex justify-center">
          <Button variant="ghost" size="sm" onClick={handleLoadMore} disabled={loadingMore}>
            {loadingMore ? t("loadingMore") : t("loadMore")}
          </Button>
        </div>
      )}
    </div>
  );
}

interface ActivityRowProps {
  item: AstroliftActivityItem;
}

function ActivityRow({ item }: ActivityRowProps) {
  const Icon = iconForEventType(item.eventType);
  const timestamp = formatRelative(item.occurredAt);

  const body = (
    <div className="flex items-start gap-3 py-3">
      <div className="bg-muted text-muted-foreground mt-0.5 rounded-md p-1.5">
        <Icon className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug">
          <span className="font-medium">{item.actorDisplay}</span>{" "}
          <span className="text-muted-foreground">{item.action.replace(/_/g, " ")}</span>{" "}
          <span className="text-foreground/90 break-all">{item.targetLabel}</span>
        </p>
        <p className="text-muted-foreground mt-0.5 text-xs">{timestamp}</p>
      </div>
    </div>
  );

  if (item.targetHref) {
    return (
      <li>
        <Link
          href={item.targetHref}
          className="hover:bg-accent/40 -mx-2 block rounded px-2 transition-colors"
        >
          {body}
        </Link>
      </li>
    );
  }
  return <li>{body}</li>;
}

/**
 * Pick an icon for a row based on the resource segment of
 * ``event_type``. We intentionally cover only the prefixes the
 * backend's ``_LIFECYCLE_EVENT_PREFIXES`` lets through; unknown
 * resources fall back to a generic activity icon so the row still
 * renders cleanly.
 */
function iconForEventType(eventType: string) {
  const resource = eventType.split(".")[0] ?? "";
  switch (resource) {
    case "deploy":
    case "deployment":
    case "promotion":
    case "rollback":
      return RocketIcon;
    case "cluster":
      return CloudCogIcon;
    case "secret":
      return KeyRoundIcon;
    case "config":
      return SettingsIcon;
    case "scale":
      return ScalingIcon;
    case "service":
    case "binding":
    case "managed_service":
      return ServerCogIcon;
    case "app":
    case "preview":
    case "drift":
    case "environment":
      return BoxIcon;
    default:
      return ActivityIcon;
  }
}

const RELATIVE_DIVISIONS: ReadonlyArray<{
  amount: number;
  unit: Intl.RelativeTimeFormatUnit;
}> = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

/**
 * Format an ISO timestamp as "3m ago" / "yesterday" / "2y ago".
 *
 * Uses ``Intl.RelativeTimeFormat`` with the browser locale so the
 * label respects the user's preference. Falls back to the raw ISO
 * string if the input fails to parse — better that than crashing
 * the row.
 */
function formatRelative(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let duration = (date.getTime() - Date.now()) / 1000;
  for (const division of RELATIVE_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return iso;
}
