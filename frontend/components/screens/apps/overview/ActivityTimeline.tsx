"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  GitCommitIcon,
  KeyRoundIcon,
  RocketIcon,
  SearchIcon,
  SettingsIcon,
} from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { Feed } from "@/components/feed/Feed";
import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useFormatters } from "@/lib/i18n/formatters";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

import type { useActivityTimeline } from "./use-activity-timeline";

// #711 — event-type filter chips. Maps each chip key to a prefix
// match on `eventType`; chip 'all' bypasses the filter.
const TYPE_FILTERS: Array<{ key: string; prefixes: string[] }> = [
  { key: "all", prefixes: [] },
  { key: "deploy", prefixes: ["deployment."] },
  { key: "config", prefixes: ["manifest.", "config.", "environment.", "app."] },
  { key: "secret", prefixes: ["secret."] },
  { key: "token", prefixes: ["deploy_token.", "app.deploy_token."] },
  { key: "alert", prefixes: ["alert."] },
];

export type ActivityTimelineViewProps = Pick<
  ReturnType<typeof useActivityTimeline>,
  "events" | "loading"
> &
  Partial<
    Pick<
      ReturnType<typeof useActivityTimeline>,
      "error" | "onRetry" | "hasMore" | "loadingMore" | "onLoadMore"
    >
  > & {
    /** The deploy heatmap (DeployActivityStrip), shown above the event feed. */
    strip?: React.ReactNode;
    span?: PanelSpan;
  };

/**
 * Recent platform events for this app (deploys, config changes, token
 * rotations and the rest) as a Feed (Leo's list rule 5): it scrolls in its
 * own frame, grouped by day, and loads older events on the cursor as the
 * reader nears the end. The chips and search narrow the events loaded so
 * far.
 */
export function ActivityTimelineView({
  events: allForApp,
  loading,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
  strip,
  span = 8,
}: ActivityTimelineViewProps) {
  const fmt = useFormatters();
  const t = useTranslations("apps.overview.activity");
  // #711 — local filter state (event type chip + free-text search).
  // Defaults to 'all' and empty so the existing summary view is
  // unchanged for operators who never touch the filters.
  const [typeKey, setTypeKey] = React.useState("all");
  const [search, setSearch] = React.useState("");

  const activeFilter = TYPE_FILTERS.find((f) => f.key === typeKey) ?? TYPE_FILTERS[0];
  const needle = search.trim().toLowerCase();

  const events = allForApp.filter((e) => {
    if (activeFilter.prefixes.length > 0) {
      const hit = activeFilter.prefixes.some((p) => e.eventType.startsWith(p));
      if (!hit) return false;
    }
    if (needle) {
      const summary = summaryFor(e).toLowerCase();
      const hay = `${summary} ${e.eventType}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });

  const filterActive = typeKey !== "all" || needle.length > 0;

  return (
    <Panel
      title={t("title")}
      icon={<ActivityIcon className="size-4" />}
      span={span}
      actions={
        events.length > 0 ? (
          <p className="text-muted-foreground text-2xs font-mono">
            {filterActive
              ? t("filteredCount", { count: events.length, total: allForApp.length })
              : t("count", { count: events.length })}
          </p>
        ) : undefined
      }
    >
      {/* #711 — filter chips + search input. Chips are buttons because
            this isn't form data, just UI state; using buttons keeps the
            a11y story simple (no aria-checked dance, just aria-pressed). */}
      {strip && <div className="mb-4 min-w-0">{strip}</div>}
      <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
        {TYPE_FILTERS.map((f) => {
          const active = f.key === typeKey;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => setTypeKey(f.key)}
              aria-pressed={active}
              className={cn(
                "text-2xs rounded-full border px-2.5 py-0.5 transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:bg-accent"
              )}
            >
              {t(`filters.${f.key}`)}
            </button>
          );
        })}
        <div className="relative ml-auto">
          <SearchIcon
            aria-hidden
            className="text-muted-foreground absolute top-1/2 left-2 size-3 -translate-y-1/2"
          />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="h-7 w-40 pl-7 text-xs"
            aria-label={t("searchLabel")}
          />
        </div>
      </div>

      {filterActive && events.length === 0 && allForApp.length > 0 ? (
        <p className="text-muted-foreground py-1 text-xs italic">
          {t.rich(needle ? "noMatchesQuery" : "noMatches", {
            filter: t(`filters.${activeFilter.key}`),
            query: needle,
            badge: (chunks) => (
              <Badge variant="secondary" className="text-2xs">
                {chunks}
              </Badge>
            ),
          })}
        </p>
      ) : (
        <Feed
          label={t("title")}
          items={events}
          keyOf={(e) => e.id}
          groupBy={{ day: (e) => e.occurredAt }}
          renderItem={(e) => (
            <TimelineRow event={e} relativeTime={fmt.formatRelativeTime(e.occurredAt)} />
          )}
          loading={loading}
          error={error}
          onRetry={onRetry}
          errorTitle={t("loadError")}
          empty={{
            icon: <ActivityIcon className="size-5" />,
            title: t("emptyTitle"),
          }}
          hasMore={hasMore}
          loadingMore={loadingMore}
          onLoadMore={onLoadMore}
          maxHeight="max-h-80"
          dense
        />
      )}
    </Panel>
  );
}

function TimelineRow({ event, relativeTime }: { event: AstroliftEvent; relativeTime: string }) {
  const summary = summaryFor(event);

  return (
    <div className="flex min-w-0 items-start gap-3 py-0.5 text-sm">
      <EventIcon
        eventType={event.eventType}
        className="text-muted-foreground mt-0.5 size-3.5 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <p className="break-words">{summary}</p>
        <p className="text-muted-foreground text-2xs">
          <span className="font-mono">{event.eventType}</span> · {relativeTime}
        </p>
      </div>
    </div>
  );
}

function EventIcon({ eventType, className }: { eventType: string; className?: string }) {
  if (eventType.startsWith("deployment.")) return <RocketIcon className={className} />;
  if (eventType.startsWith("manifest.") || eventType.startsWith("config.")) {
    return <GitCommitIcon className={className} />;
  }
  if (eventType.startsWith("deploy_token.") || eventType.startsWith("app.deploy_token.")) {
    return <KeyRoundIcon className={className} />;
  }
  if (eventType.startsWith("environment.") || eventType.startsWith("app.")) {
    return <SettingsIcon className={className} />;
  }
  if (eventType.startsWith("alert.")) return <AlertTriangleIcon className={className} />;
  return <ActivityIcon className={className} />;
}

function summaryFor(event: AstroliftEvent): string {
  // Pull a human-readable summary from the payload when present;
  // otherwise fall back to a slugified type as a last-resort label so
  // the row is never blank.
  const payload = event.payload as Record<string, unknown> | null | undefined;
  if (payload) {
    for (const key of ["summary", "message", "title", "description"]) {
      const candidate = payload[key];
      if (typeof candidate === "string" && candidate.length > 0) {
        return candidate;
      }
    }
    if (typeof payload.status === "string" && typeof payload.imageTag === "string") {
      return `${payload.imageTag} → ${payload.status}`;
    }
  }
  return event.eventType.replace(/_/g, " ").replace(/\./g, " · ");
}
