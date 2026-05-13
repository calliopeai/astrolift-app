"use client";

import { useQuery } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  GitCommitIcon,
  KeyRoundIcon,
  RocketIcon,
  SettingsIcon,
} from "lucide-react";
import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

interface EventsResp {
  astroliftEvents: AstroliftEvent[];
}

interface Props {
  appId: string;
  limit?: number;
}

/**
 * Recent platform events for this app — deploys, config changes, token
 * rotations, etc. The events feed is org-wide, so we filter client-side on
 * `registeredAppId`. The query is cheap (capped at 100) and shared with
 * the dashboard, so this incurs no extra round-trip when the dashboard
 * already primed it.
 */
export function ActivityTimeline({ appId, limit = 20 }: Props) {
  const fmt = useFormatters();
  const { data, loading } = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });

  const events = (data?.astroliftEvents ?? [])
    .filter((e) => e.registeredAppId === appId)
    .slice(0, limit);

  return (
    <section className="rounded-lg border p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <ActivityIcon className="text-muted-foreground size-4" />
          Activity
        </h2>
        {events.length > 0 && (
          <p className="text-muted-foreground text-[11px]">Last {events.length} events</p>
        )}
      </div>

      {loading && events.length === 0 ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : events.length === 0 ? (
        <p className="text-muted-foreground py-1 text-xs italic">No recent events for this app.</p>
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
        <p className="text-muted-foreground text-[11px]">
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
