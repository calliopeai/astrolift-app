"use client";

import { useQuery } from "@apollo/client/react";
import { ActivityIcon, ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import NextLink from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LIST_EVENTS,
  LIST_EVENTS_AGGREGATED,
} from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface RawResp {
  astroliftEvents: AstroliftEvent[];
}

interface AggregatedEvent {
  representative: AstroliftEvent;
  count: number;
  firstAt: string;
  lastAt: string;
  eventType: string;
  resourceKind: string;
  resourceId: string;
}

interface AggResp {
  astroliftEventsAggregated: AggregatedEvent[];
}

const DEFAULT_AGGREGATE_WINDOW_SECONDS = 300;

export function EventsClient() {
  const t = useTranslations("lists.events");
  const [filter, setFilter] = React.useState("");
  const [aggregate, setAggregate] = React.useState(true);

  return (
    <PageShell title={t("title")} description={t("description")}>
      <div className="flex items-center gap-3">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("filterPlaceholder")}
          className="max-w-xs"
        />
        <Label className="text-muted-foreground flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={aggregate}
            onChange={(e) => setAggregate(e.target.checked)}
            aria-label={t("aggregateToggle")}
          />
          {t("aggregateToggle")}
        </Label>
      </div>

      {aggregate ? (
        <AggregatedList eventTypeFilter={filter} />
      ) : (
        <RawList eventTypeFilter={filter} />
      )}
    </PageShell>
  );
}

function RawList({ eventTypeFilter }: { eventTypeFilter: string }) {
  const t = useTranslations("lists.events");
  const { data, loading } = useQuery<RawResp>(LIST_EVENTS, {
    variables: { limit: 200, eventType: eventTypeFilter || null },
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });
  const list = data?.astroliftEvents ?? [];

  if (loading && list.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (list.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<ActivityIcon className="size-5" />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <ul className="divide-y">
          {list.map((e) => (
            <EventRow key={e.id} event={e} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AggregatedList({ eventTypeFilter }: { eventTypeFilter: string }) {
  const t = useTranslations("lists.events");
  const { data, loading } = useQuery<AggResp>(LIST_EVENTS_AGGREGATED, {
    variables: {
      limit: 200,
      eventType: eventTypeFilter || null,
      aggregateWindowSeconds: DEFAULT_AGGREGATE_WINDOW_SECONDS,
    },
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });
  const buckets = data?.astroliftEventsAggregated ?? [];

  if (loading && buckets.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (buckets.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<ActivityIcon className="size-5" />}
            title={t("emptyTitle")}
            description={t("emptyDescription")}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <ul className="divide-y">
          {buckets.map((b) => (
            <BucketRow
              key={`${b.eventType}|${b.resourceKind}|${b.resourceId}|${b.representative.id}`}
              bucket={b}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function BucketRow({ bucket }: { bucket: AggregatedEvent }) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();
  const [expanded, setExpanded] = React.useState(false);
  // Expand: re-query unaggregated events filtered to this bucket's event type
  // and let the row show the members. The backend doesn't (today) have a
  // tighter filter than event_type, so the FE narrows by resource key
  // client-side — cheap because each bucket's source set is bounded.
  return (
    <li className="p-4">
      <div className="flex items-start gap-4">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-muted-foreground hover:bg-muted mt-0.5 rounded-md p-1"
          aria-label={expanded ? t("collapseLabel") : t("expandLabel")}
        >
          {expanded ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronRightIcon className="size-4" />
          )}
        </button>
        <div className="bg-primary/10 text-primary mt-0.5 rounded-md p-2">
          <ActivityIcon className="size-4" />
        </div>
        <div className="flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-xs">
              {bucket.eventType}
            </Badge>
            {bucket.count > 1 && (
              <Badge variant="secondary" className="text-xs">
                {t("aggregateBadge", { count: bucket.count })}
              </Badge>
            )}
            <SourceBadge
              resourceKind={bucket.resourceKind}
              resourceId={bucket.resourceId}
              payload={bucket.representative.payload}
            />
            <span className="text-muted-foreground text-xs">
              {bucket.count > 1
                ? `${fmt.formatDateTime(bucket.firstAt)} → ${fmt.formatDateTime(bucket.lastAt)}`
                : fmt.formatDateTime(bucket.lastAt)}
            </span>
          </div>
          {expanded && (
            <BucketMembers
              eventType={bucket.eventType}
              resourceKind={bucket.resourceKind}
              resourceId={bucket.resourceId}
            />
          )}
        </div>
      </div>
    </li>
  );
}

function BucketMembers({
  eventType,
  resourceKind,
  resourceId,
}: {
  eventType: string;
  resourceKind: string;
  resourceId: string;
}) {
  const fmt = useFormatters();
  const { data, loading } = useQuery<RawResp>(LIST_EVENTS, {
    variables: { limit: 200, eventType },
    fetchPolicy: "cache-and-network",
  });
  const list = (data?.astroliftEvents ?? []).filter(
    (e) =>
      (e.resourceKind ?? "") === (resourceKind ?? "") &&
      (e.resourceId ?? "") === (resourceId ?? ""),
  );

  if (loading && list.length === 0) {
    return <Skeleton className="mt-3 h-12 w-full" />;
  }
  if (list.length === 0) {
    return null;
  }

  return (
    <ul className="bg-muted/30 mt-3 divide-y rounded-md border">
      {list.map((e) => (
        <li key={e.id} className="p-3">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono text-xs">
              {e.eventType}
            </Badge>
            <span className="text-muted-foreground text-xs">
              {fmt.formatDateTime(e.occurredAt)}
            </span>
          </div>
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

function EventRow({ event }: { event: AstroliftEvent }) {
  const fmt = useFormatters();
  return (
    <li className="flex items-start gap-4 p-4">
      <div className="bg-primary/10 text-primary mt-0.5 rounded-md p-2">
        <ActivityIcon className="size-4" />
      </div>
      <div className="flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-xs">
            {event.eventType}
          </Badge>
          <SourceBadge
            resourceKind={event.resourceKind ?? ""}
            resourceId={event.resourceId ?? ""}
            payload={event.payload}
          />
          <span className="text-muted-foreground text-xs">
            {fmt.formatDateTime(event.occurredAt)}
          </span>
        </div>
        {Object.keys(event.payload ?? {}).length > 0 && (
          <pre className="bg-muted text-muted-foreground mt-2 overflow-x-auto rounded-md p-2 text-xs">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        )}
      </div>
    </li>
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
  const display = resourceKind
    ? `${resourceKind}:${resourceId || "?"}`
    : (label ?? "");
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
      className="inline-flex items-center"
      aria-label={`${t("sourceLabel")}: ${display}`}
    >
      <Badge
        variant="secondary"
        className="hover:bg-primary/10 cursor-pointer font-mono text-xs"
      >
        {display}
      </Badge>
    </NextLink>
  );
}

export function resolveSourceHref(
  resourceKind: string,
  resourceId: string,
  payload: Record<string, unknown>,
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
    const workloadSlug =
      (typeof payload.workload_slug === "string" && payload.workload_slug) ||
      id;
    if (appSlug && workloadSlug) {
      return `/apps/${encodeURIComponent(appSlug)}/workloads/${encodeURIComponent(workloadSlug)}`;
    }
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "managed_service" || kind === "managedservice") {
    const msvcId =
      (typeof payload.managed_service_id === "string" &&
        payload.managed_service_id) ||
      id;
    if (appSlug && msvcId) {
      return `/apps/${encodeURIComponent(appSlug)}/managed-services/${encodeURIComponent(msvcId)}`;
    }
    return msvcId ? `/services` : null;
  }
  return null;
}
