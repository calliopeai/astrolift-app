"use client";

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
import { useFormatter, useNow, useTranslations } from "next-intl";

import { Feed } from "@/components/feed/Feed";
import type { AstroliftActivityItem } from "@/graphql/operations/operations.types";

export interface ActivityFeedProps {
  items: AstroliftActivityItem[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetry?: () => void;
  /** Frame height class; see Feed. */
  maxHeight?: string;
}

/**
 * Home's "Activity" panel feed (#435 scope A).
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
 * A thin wrapper over Feed (list rule 5): the frame scrolls on its own,
 * older pages load as the reader nears the end, grouped by day.
 *
 * Pure (Storybook first): the page and its load-more come from
 * useRecentActivity, which pages by cursor over ``(occurred_at, guid)``.
 */
export function ActivityFeed({
  items,
  loading,
  error,
  hasMore,
  loadingMore,
  onLoadMore,
  onRetry,
  maxHeight,
}: ActivityFeedProps) {
  const t = useTranslations("overview.activity");
  const format = useFormatter();
  const now = useNow({ updateInterval: 60_000 });
  return (
    <Feed
      label={t("title")}
      items={items}
      keyOf={(item) => item.id}
      renderItem={(item) => (
        <ActivityRow
          item={item}
          timestamp={
            Number.isFinite(Date.parse(item.occurredAt))
              ? format.relativeTime(new Date(item.occurredAt), now)
              : item.occurredAt
          }
        />
      )}
      groupBy={BY_DAY}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <ActivityIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      hasMore={hasMore}
      loadingMore={loadingMore}
      onLoadMore={onLoadMore}
      errorTitle={t("errorTitle")}
      loadOlderLabel={t("loadMore")}
      loadingOlderLabel={t("loadingMore")}
      maxHeight={maxHeight}
    />
  );
}

const BY_DAY = { day: (item: AstroliftActivityItem) => item.occurredAt };

interface ActivityRowProps {
  item: AstroliftActivityItem;
  timestamp: string;
}

function ActivityRow({ item, timestamp }: ActivityRowProps) {
  const Icon = iconForEventType(item.eventType);

  const body = (
    <div className="flex min-w-0 items-start gap-3">
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

  // Feed puts each item in its own <li>.
  if (item.targetHref) {
    return (
      <Link
        href={item.targetHref}
        className="hover:bg-accent/40 -mx-2 block rounded px-2 transition-colors"
      >
        {body}
      </Link>
    );
  }
  return body;
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
