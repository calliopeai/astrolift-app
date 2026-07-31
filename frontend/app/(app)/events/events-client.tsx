"use client";

import { useQuery } from "@apollo/client/react";
import { ActivityIcon, ChevronRightIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import NextLink from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { DataTable, useCursorTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/viz";
import {
  LIST_EVENTS_AGGREGATED_PAGE,
  LIST_EVENTS_PAGE,
} from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface AggregatedEvent {
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

const DEFAULT_AGGREGATE_WINDOW_SECONDS = 300;
const RATE_WINDOW_DAYS = 14;
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
function EventRate() {
  const { data } = useQuery<RawPageResp>(LIST_EVENTS_PAGE, {
    variables: { limit: RATE_SAMPLE_LIMIT },
    fetchPolicy: "cache-and-network",
    pollInterval: RATE_POLL_MS,
  });
  const days = perDayCounts((data?.astroliftEventsPage.items ?? []).map((e) => e.occurredAt));
  const total = days.reduce((a, b) => a + b, 0);
  if (total === 0) return null;
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-3">
        <div>
          <p className="text-muted-foreground text-2xs tracking-wide uppercase">
            Last {RATE_WINDOW_DAYS} days
          </p>
          <p className="text-xl font-semibold tabular-nums">{total}</p>
        </div>
        <Sparkline
          data={days}
          width={220}
          height={40}
          variant="area"
          className="text-chart-1"
          ariaLabel="Event rate over the last two weeks"
        />
      </CardContent>
    </Card>
  );
}

export function EventsClient() {
  const t = useTranslations("lists.events");
  const [aggregate, setAggregate] = React.useState(true);

  // Rides in DataTable's toolbar, beside the search box. The box is the
  // controller's own: server-side, debounced, and matching on event type,
  // resource kind/id and app slug — it replaces both the per-keystroke
  // `eventType` input and the client-side filter that sat on top of it.
  const aggregateToggle = (
    <Label className="text-muted-foreground flex items-center gap-2 text-xs">
      <input
        type="checkbox"
        checked={aggregate}
        onChange={(e) => setAggregate(e.target.checked)}
        aria-label={t("aggregateToggle")}
      />
      {t("aggregateToggle")}
    </Label>
  );

  return (
    <PageShell title={t("title")} description={t("description")}>
      <EventRate />
      {/* Both lists read and write `?ev-q=`, so toggling grouping keeps the
          search term the operator typed. Only one is ever mounted. */}
      {aggregate ? (
        <AggregatedList toolbar={aggregateToggle} />
      ) : (
        <RawList toolbar={aggregateToggle} />
      )}
    </PageShell>
  );
}

function RawList({ toolbar }: { toolbar: React.ReactNode }) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();

  const table = useCursorTable<AstroliftEvent>({
    query: LIST_EVENTS_PAGE,
    extract: (d) => (d as RawPageResp | undefined)?.astroliftEventsPage,
    searchVariable: "search",
    urlKey: "ev",
    pollInterval: EVENT_POLL_MS,
  });

  // No sort controls: `astroliftEventsPage` takes no sort argument (it
  // seeks on `occurred_at, guid`, newest first), and reordering the page in
  // hand while the rest of the stream sits on the server is wrong at every
  // page boundary.
  const columns: Column<AstroliftEvent>[] = [
    {
      id: "type",
      header: "Type",
      cell: (e) => (
        <Badge variant="outline" className="font-mono text-xs">
          {e.eventType}
        </Badge>
      ),
    },
    {
      id: "source",
      header: t("sourceLabel"),
      // The row is a stretched link (`rowHref`) whose ::after covers every
      // cell; the deep-link badge has to sit above it to stay clickable.
      cellClassName: "relative z-10",
      cell: (e) => (
        <SourceBadge
          resourceKind={e.resourceKind ?? ""}
          resourceId={e.resourceId ?? ""}
          payload={e.payload}
        />
      ),
    },
    {
      id: "payload",
      header: "Payload",
      cell: (e) => <PayloadPreview payload={e.payload} />,
    },
    {
      id: "time",
      header: "Time",
      width: "w-52",
      cellClassName: "text-muted-foreground text-xs",
      cell: (e) => fmt.formatDateTime(e.occurredAt),
    },
  ];

  return (
    <DataTable
      label="Events"
      controller={table}
      columns={columns}
      getRowId={(e) => e.id}
      rowHref={(e) => `/events/${e.id}`}
      toolbar={toolbar}
      searchPlaceholder={t("filterPlaceholder")}
      empty={{
        icon: <ActivityIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      emptyFiltered={{
        title: "No matching events",
        description:
          "No event matches that search. It looks at the event type, the resource kind and id, and the app slug — try a shorter term, or clear the search to see the whole stream.",
      }}
    />
  );
}

function AggregatedList({ toolbar }: { toolbar: React.ReactNode }) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();
  const [openBucket, setOpenBucket] = React.useState<AggregatedEvent | null>(null);

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

  const columns: Column<AggregatedEvent>[] = [
    {
      id: "type",
      header: "Type",
      cell: (b) => (
        <span className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {b.eventType}
          </Badge>
          {b.count > 1 && (
            <Badge variant="secondary" className="text-xs">
              {t("aggregateBadge", { count: b.count })}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "source",
      header: t("sourceLabel"),
      cell: (b) => (
        <SourceBadge
          resourceKind={b.resourceKind}
          resourceId={b.resourceId}
          payload={b.representative.payload}
        />
      ),
    },
    {
      id: "window",
      header: "Window",
      cellClassName: "text-muted-foreground text-xs",
      cell: (b) =>
        b.count > 1
          ? `${fmt.formatDateTime(b.firstAt)} → ${fmt.formatDateTime(b.lastAt)}`
          : fmt.formatDateTime(b.lastAt),
    },
    {
      id: "members",
      header: <span className="sr-only">Members</span>,
      align: "right",
      width: "w-32",
      // The row opens the same dialog, but a `<tr>` onClick is not
      // keyboard-reachable — this button is what makes the bucket's
      // members openable without a mouse. Its visible text is its
      // accessible name; the member count is already in the type cell.
      cell: (b) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={(ev) => {
            ev.stopPropagation();
            setOpenBucket(b);
          }}
        >
          {t("expandLabel")}
          <ChevronRightIcon className="size-3.5" />
        </Button>
      ),
    },
  ];

  // Two buckets of the same event type differ only by resource, so the
  // dialog has to say which one it opened.
  const bucketSummary = openBucket
    ? [
        openBucket.count > 1
          ? `${openBucket.count} events between ${fmt.formatDateTime(openBucket.firstAt)} and ${fmt.formatDateTime(openBucket.lastAt)}`
          : `1 event at ${fmt.formatDateTime(openBucket.lastAt)}`,
        openBucket.resourceKind || openBucket.resourceId
          ? `${openBucket.resourceKind || "?"}:${openBucket.resourceId || "?"}`
          : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <>
      <DataTable
        label="Event groups"
        controller={table}
        columns={columns}
        getRowId={(b) => `${b.eventType}|${b.resourceKind}|${b.resourceId}|${b.representative.id}`}
        // Buckets are folds, not rows, so they have no URL of their own to
        // link to: activating one opens its members instead of navigating.
        onRowActivate={(b) => setOpenBucket(b)}
        toolbar={toolbar}
        searchPlaceholder={t("filterPlaceholder")}
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        emptyFiltered={{
          title: "No matching event groups",
          description:
            "No group matches that search. It looks at the event type, the resource kind and id, and the app slug — try a shorter term, or clear the search to see the whole stream.",
        }}
      />

      <Dialog
        open={openBucket !== null}
        onOpenChange={(next) => {
          if (!next) setOpenBucket(null);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">{openBucket?.eventType}</DialogTitle>
            <DialogDescription>{bucketSummary}</DialogDescription>
          </DialogHeader>
          {openBucket && <BucketMembers bucket={openBucket} />}
        </DialogContent>
      </Dialog>
    </>
  );
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
function BucketMembers({ bucket }: { bucket: AggregatedEvent }) {
  const fmt = useFormatters();
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

  if (loading && members.length === 0) {
    return <Skeleton className="h-24 w-full" />;
  }
  if (members.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        These events have aged out of the window this view reads. Open the representative event for
        its full payload.
      </p>
    );
  }

  return (
    <ul className="divide-y rounded-md border">
      {members.map((e) => (
        <li key={e.id} className="p-3">
          <NextLink
            href={`/events/${e.id}`}
            className="hover:bg-accent/30 focus-visible:outline-ring -m-1 flex flex-wrap items-center gap-2 rounded-md p-1 focus-visible:outline-2"
            aria-label={`Open event ${e.eventType}`}
          >
            <Badge variant="outline" className="font-mono text-xs">
              {e.eventType}
            </Badge>
            <span className="text-muted-foreground text-xs">
              {fmt.formatDateTime(e.occurredAt)}
            </span>
          </NextLink>
          {Object.keys(e.payload ?? {}).length > 0 && (
            <pre className="bg-muted text-muted-foreground mt-2 overflow-x-auto rounded-md p-2 text-xs">
              {JSON.stringify(e.payload, null, 2)}
            </pre>
          )}
        </li>
      ))}
    </ul>
  );
}

/** One-line payload preview; the full document lives on the detail page. */
function PayloadPreview({ payload }: { payload: Record<string, unknown> | null | undefined }) {
  if (Object.keys(payload ?? {}).length === 0) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }
  const text = JSON.stringify(payload);
  return (
    <span
      className="text-muted-foreground text-2xs block max-w-md truncate font-mono"
      title={text.slice(0, 500)}
    >
      {text}
    </span>
  );
}

/**
 * Resolve an event's source to a deep link badge (#434 scope B).
 *
 * Mapping:
 *   - app           → /apps/<slug>
 *   - cluster       → /clusters/<id>
 *   - workload      → /apps/<slug>/workloads/<workloadSlug>
 *   - managed_service → /apps/<slug>/managed-services/<id>
 *   - everything else → text-only badge, no link.
 *
 * App-shaped events stamp resource_id with the slug today; payloads
 * may also carry ``app_slug`` (sometimes both, in which case the
 * payload wins because it's the more authoritative source).
 */
function SourceBadge({
  resourceKind,
  resourceId,
  payload,
}: {
  resourceKind: string;
  resourceId: string;
  payload: Record<string, unknown> | null | undefined;
}) {
  const t = useTranslations("lists.events");
  if (!resourceKind && !resourceId) return null;
  const href = resolveSourceHref(resourceKind, resourceId, payload ?? {});
  const label = resourceId || resourceKind;
  const display = resourceKind ? `${resourceKind}:${resourceId || "?"}` : (label ?? "");
  if (!href) {
    return (
      <Badge variant="outline" className="font-mono text-xs">
        {display}
      </Badge>
    );
  }
  return (
    <NextLink
      href={href}
      onClick={(ev) => ev.stopPropagation()}
      className="inline-flex items-center"
      aria-label={`${t("sourceLabel")}: ${display}`}
    >
      <Badge variant="secondary" className="hover:bg-primary/10 cursor-pointer font-mono text-xs">
        {display}
      </Badge>
    </NextLink>
  );
}

export function resolveSourceHref(
  resourceKind: string,
  resourceId: string,
  payload: Record<string, unknown>
): string | null {
  const kind = (resourceKind ?? "").toLowerCase();
  const id = (resourceId ?? "").trim();
  const appSlug =
    (typeof payload.app_slug === "string" && payload.app_slug) ||
    (typeof payload.slug === "string" && payload.slug) ||
    (kind === "app" ? id : "");

  if (!kind && !id) return null;

  if (kind === "app") {
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "cluster") {
    return id ? `/clusters/${encodeURIComponent(id)}` : null;
  }
  if (kind === "workload") {
    const workloadSlug = (typeof payload.workload_slug === "string" && payload.workload_slug) || id;
    if (appSlug && workloadSlug) {
      return `/apps/${encodeURIComponent(appSlug)}/workloads/${encodeURIComponent(workloadSlug)}`;
    }
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "managed_service" || kind === "managedservice") {
    const msvcId =
      (typeof payload.managed_service_id === "string" && payload.managed_service_id) || id;
    if (appSlug && msvcId) {
      return `/apps/${encodeURIComponent(appSlug)}/managed-services/${encodeURIComponent(msvcId)}`;
    }
    return msvcId ? `/services` : null;
  }
  return null;
}
