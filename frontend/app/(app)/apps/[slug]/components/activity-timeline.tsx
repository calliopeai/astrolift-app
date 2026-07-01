"use client";

import { useQuery } from "@apollo/client/react";
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

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useFormatters } from "@/lib/i18n/formatters";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

// #711 — event-type filter chips. Maps each chip key to a prefix
// match on `eventType`; chip 'all' bypasses the filter.
const TYPE_FILTERS: Array<{ key: string; label: string; prefixes: string[] }> = [
  { key: "all", label: "All", prefixes: [] },
  { key: "deploy", label: "Deploys", prefixes: ["deployment."] },
  { key: "config", label: "Config", prefixes: ["manifest.", "config.", "environment.", "app."] },
  { key: "secret", label: "Secrets", prefixes: ["secret."] },
  { key: "token", label: "Tokens", prefixes: ["deploy_token.", "app.deploy_token."] },
  { key: "alert", label: "Alerts", prefixes: ["alert."] },
];

interface EventsResp {
  astroliftEvents: AstroliftEvent[];
}

interface Props {
  /** App slug — used for server-side filtering so only this app's events are fetched. */
  appSlug: string;
  limit?: number;
}

/**
 * Recent platform events for this app — deploys, config changes, token
 * rotations, etc. Server-side filtered by appSlug so we only load events
 * belonging to this app (fixes the empty-feed bug where client-side filtering
 * compared the app GUID against the integer PK serialised as a string).
 */
export function ActivityTimeline({ appSlug, limit = 20 }: Props) {
  const fmt = useFormatters();
  // #711 — local filter state (event type chip + free-text search).
  // Defaults to 'all' and empty so the existing summary view is
  // unchanged for operators who never touch the filters.
  const [typeKey, setTypeKey] = React.useState("all");
  const [search, setSearch] = React.useState("");

  const { data, loading } = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 100, appSlug },
    fetchPolicy: "cache-and-network",
  });

  const allForApp = data?.astroliftEvents ?? [];

  const activeFilter = TYPE_FILTERS.find((f) => f.key === typeKey) ?? TYPE_FILTERS[0];
  const needle = search.trim().toLowerCase();

  const events = allForApp
    .filter((e) => {
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
    })
    .slice(0, limit);

  const filterActive = typeKey !== "all" || needle.length > 0;

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <ActivityIcon className="text-muted-foreground size-4" />
          Activity
        </h2>
        {events.length > 0 && (
          <p className="text-muted-foreground text-2xs">
            {filterActive
              ? `${events.length} of ${allForApp.length} events`
              : `Last ${events.length} events`}
          </p>
        )}
      </div>

      {/* #711 — filter chips + search input. Chips are buttons because
          this isn't form data, just UI state; using buttons keeps the
          a11y story simple (no aria-checked dance, just aria-pressed). */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {TYPE_FILTERS.map((f) => {
          const active = f.key === typeKey;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => setTypeKey(f.key)}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-2xs transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border text-muted-foreground hover:bg-accent",
              )}
            >
              {f.label}
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
            placeholder="Search…"
            className="h-7 w-40 pl-7 text-xs"
            aria-label="Search activity"
          />
        </div>
      </div>

      {loading && events.length === 0 ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : events.length === 0 ? (
        <p className="text-muted-foreground py-1 text-xs italic">
          {filterActive ? (
            <>
              No events match{" "}
              <Badge variant="secondary" className="text-2xs">
                {activeFilter.label}
                {needle ? ` · "${needle}"` : ""}
              </Badge>
              .
            </>
          ) : (
            "No recent events for this app."
          )}
        </p>
      ) : (
        <ol className="divide-border max-h-80 divide-y overflow-y-auto">
          {events.map((e) => (
            <TimelineRow key={e.id} event={e} relativeTime={fmt.formatRelativeTime(e.occurredAt)} />
          ))}
        </ol>
      )}
    </section>
  );
}

function TimelineRow({ event, relativeTime }: { event: AstroliftEvent; relativeTime: string }) {
  const summary = summaryFor(event);

  return (
    <li className="flex items-start gap-3 py-2.5 text-sm">
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
    </li>
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
