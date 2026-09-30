"use client";

import { useQuery } from "@apollo/client/react";

import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import {
  GET_EVENT,
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
 * `AstroliftEventPage` deliberately carries no `totalCount`: the stream is
 * unbounded and counting it is a table scan, so the envelope here is
 * narrower than the aggregated one.
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

interface DetailResp {
  astroliftEvent: AstroliftEvent | null;
}

const DEFAULT_AGGREGATE_WINDOW_SECONDS = 300;
export const RATE_WINDOW_DAYS = 14;
// The stream is the one surface where a stale row is actively misleading,
// so both feeds keep the 5s poll; new events wait behind the "n new" pill.
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
 * than from either feed's page, and deliberately unfiltered: the card is
 * the baseline the operator searches *against*, so it must not move under
 * them while they type. Both views share it — grouping is a presentation
 * of the same raw stream, so the per-day counts are identical either way.
 */
export function useEventRate() {
  const { data, loading, error, refetch } = useQuery<RawPageResp>(LIST_EVENTS_PAGE, {
    variables: { limit: RATE_SAMPLE_LIMIT },
    fetchPolicy: "cache-and-network",
    pollInterval: RATE_POLL_MS,
  });
  const days = perDayCounts((data?.astroliftEventsPage.items ?? []).map((e) => e.occurredAt));
  const total = days.reduce((a, b) => a + b, 0);
  return {
    days,
    total,
    loading: loading && !data,
    error: data ? null : (error ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}

/**
 * The raw stream as a Feed (list rule 5), newest first, loading older
 * events on the server's cursor as the reader nears the end. `search` is
 * the settled (debounced) term: server-side, matching on event type,
 * resource kind/id and app slug.
 */
export function useRawEvents(search: string) {
  const { feed } = useCursorFeed<RawPageResp, AstroliftEvent>(LIST_EVENTS_PAGE, {
    variables: { search: search.trim() || null },
    select: (d) => d?.astroliftEventsPage,
    keyOf: (e) => e.id,
    pollInterval: EVENT_POLL_MS,
  });
  return { events: feed };
}

/** A bucket's identity: its fold key plus the event that stands for it. */
export function bucketKey(b: AggregatedEvent): string {
  return `${b.eventType}|${b.resourceKind}|${b.resourceId}|${b.representative.id}`;
}

/** The stream folded server-side into buckets of repeats, as a Feed. */
export function useAggregatedEvents(search: string) {
  const { feed } = useCursorFeed<AggPageResp, AggregatedEvent>(LIST_EVENTS_AGGREGATED_PAGE, {
    // The roll-up window is part of the question: changing it starts over.
    variables: {
      aggregateWindowSeconds: DEFAULT_AGGREGATE_WINDOW_SECONDS,
      search: search.trim() || null,
    },
    select: (d) => d?.astroliftEventsAggregatedPage,
    keyOf: bucketKey,
    pollInterval: EVENT_POLL_MS,
  });
  return { buckets: feed };
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

/** A direct owner-filtered read keeps old deep links independent of the stream window. */
export function useEventDetail(id: string) {
  const { data, loading, error, refetch } = useQuery<DetailResp>(GET_EVENT, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });
  return {
    id,
    event: data?.astroliftEvent ?? null,
    loading: loading && !data,
    error: data ? null : error?.message,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
