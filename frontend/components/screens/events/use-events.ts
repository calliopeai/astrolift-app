"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useCursorTable } from "@/components/data-table";
import {
  LIST_EVENTS,
  LIST_EVENTS_AGGREGATED_PAGE,
  LIST_EVENTS_PAGE,
} from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

export interface AggregatedEvent {
  representative: AstroliftEvent;
  count: number;
  firstAt: string;
  lastAt: string;
  eventType: string;
  resourceKind: string;
  resourceId: string;
}

/**
 * `AstroliftEventPage` deliberately carries no `totalCount` — the stream is
 * unbounded and counting it is a table scan — so the envelope here is
 * narrower than the aggregated one. `useCursorTable` treats a missing count
 * as null and DataTable simply omits the "N results" readout.
 */
interface RawPageResp {
  astroliftEventsPage: {
    items: AstroliftEvent[];
    nextCursor?: string | null;
  };
}

interface AggPageResp {
  astroliftEventsAggregatedPage: {
    items: AggregatedEvent[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

interface ListResp {
  astroliftEvents: AstroliftEvent[];
}

const DEFAULT_AGGREGATE_WINDOW_SECONDS = 300;
export const RATE_WINDOW_DAYS = 14;
// The stream is the one surface where a stale row is actively misleading,
// so both tables keep the 5s poll the pre-pagination version had.
const EVENT_POLL_MS = 5000;
// The rate card samples the most recent N events rather than the page on
// screen: a sparkline of 25 rows is not a 14-day rate. Its own poll is slow
// on purpose — a two-week line does not move in five seconds.
const RATE_SAMPLE_LIMIT = 200;
const RATE_POLL_MS = 60000;
// A bucket's members are consecutive in the raw stream, so this window is
// generous. Narrowed server-side by event type + resource before the exact
// client-side match below.
const BUCKET_MEMBER_LIMIT = 200;

// Bucket timestamps into the last N days (oldest → newest) so the event
// stream gets an at-a-glance velocity line.
function perDayCounts(timestamps: string[], windowDays = RATE_WINDOW_DAYS): number[] {
  const days = new Array<number>(windowDays).fill(0);
  const now = Date.now();
  const dayMs = 86_400_000;
  for (const ts of timestamps) {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) continue;
    const ago = Math.floor((now - t) / dayMs);
    if (ago >= 0 && ago < windowDays) days[windowDays - 1 - ago] += 1;
  }
  return days;
}

/**
 * Event velocity over the last two weeks.
 *
 * Sourced from its own bounded fetch (the 200 most recent events) rather
 * than from either table's page, and deliberately unfiltered: the card is
 * the baseline the operator searches *against*, so it must not move under
 * them while they type. Both views share it — grouping is a presentation
 * of the same raw stream, so the per-day counts are identical either way.
 */
export function useEventRate() {
  const { data } = useQuery<RawPageResp>(LIST_EVENTS_PAGE, {
    variables: { limit: RATE_SAMPLE_LIMIT },
    fetchPolicy: "cache-and-network",
    pollInterval: RATE_POLL_MS,
  });
  const days = perDayCounts((data?.astroliftEventsPage.items ?? []).map((e) => e.occurredAt));
  const total = days.reduce((a, b) => a + b, 0);
  return { days, total };
}

/**
 * The raw stream, cursor-paged. The search box is the controller's own:
 * server-side, debounced, and matching on event type, resource kind/id and
 * app slug.
 */
export function useRawEvents() {
  const table = useCursorTable<AstroliftEvent>({
    query: LIST_EVENTS_PAGE,
    extract: (d) => (d as RawPageResp | undefined)?.astroliftEventsPage,
    searchVariable: "search",
    urlKey: "ev",
    pollInterval: EVENT_POLL_MS,
  });
  return { table };
}

/** The stream folded server-side into buckets of repeats. */
export function useAggregatedEvents() {
  const table = useCursorTable<AggregatedEvent>({
    query: LIST_EVENTS_AGGREGATED_PAGE,
    // The roll-up window is part of the question, so it is a static
    // controller variable: changing it restarts the walk at page one.
    variables: { aggregateWindowSeconds: DEFAULT_AGGREGATE_WINDOW_SECONDS },
    extract: (d) => (d as AggPageResp | undefined)?.astroliftEventsAggregatedPage,
    searchVariable: "search",
    urlKey: "ev",
    pollInterval: EVENT_POLL_MS,
  });
  return { table };
}

/**
 * The raw events folded into one bucket.
 *
 * The backend has no "members of this bucket" field, so the narrowing is
 * the same one the inline expander did: a bounded window of the stream at
 * this event type, matched exactly on the bucket's resource. The `search`
 * argument (#1235) now pushes the resource half of that filter to the
 * server as well, so the window is far likelier to hold every member.
 */
export function useBucketMembers(bucket: AggregatedEvent) {
  const { data, loading } = useQuery<RawPageResp>(LIST_EVENTS_PAGE, {
    variables: {
      limit: BUCKET_MEMBER_LIMIT,
      eventType: bucket.eventType,
      search: bucket.resourceId || null,
    },
    fetchPolicy: "cache-and-network",
  });

  const members = (data?.astroliftEventsPage.items ?? []).filter(
    (e) => e.resourceKind === bucket.resourceKind && e.resourceId === bucket.resourceId
  );
  return { loading, members };
}

/**
 * Event detail (#1106) — full payload + metadata for a single platform event.
 * Reuses the unfiltered LIST_EVENTS window (no singular query exists); a cold
 * deep-link resolves as long as the event is within the recent 200.
 */
export function useEventDetail(id: string) {
  const { data, loading } = useQuery<ListResp>(LIST_EVENTS, {
    variables: { limit: 200, eventType: null },
    fetchPolicy: "cache-and-network",
  });

  const event = React.useMemo(
    () => (data?.astroliftEvents ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );
  return { id, event, loading };
}
