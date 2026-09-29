"use client";

import { ActivityIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { PayloadPreview, SourceBadge } from "./EventSourceBadge";
import type { useRawEvents } from "./use-events";

export type RawEventsListProps = ReturnType<typeof useRawEvents>;

/**
 * The ungrouped stream: one line per event, each a link to its detail, in
 * a Feed that loads older events as the reader nears the end (list rule 5).
 * The order is the server's (`occurred_at, guid`, newest first). Pure.
 */
export function RawEventsList({ events }: RawEventsListProps) {
  const t = useTranslations("lists.events");
  const fmt = useFormatters();

  return (
    <Feed<AstroliftEvent>
      label="Events"
      {...events}
      keyOf={(e) => e.id}
      groupBy={{ day: (e) => e.occurredAt }}
      maxHeight="max-h-160"
      empty={{
        icon: <ActivityIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      renderItem={(e) => (
        <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1 px-1">
          <Link
            href={`/events/${e.id}`}
            className="min-w-0 font-mono text-xs [overflow-wrap:anywhere] hover:underline"
          >
            <Badge variant="outline" className="max-w-full font-mono text-xs">
              <span className="min-w-0 truncate">{e.eventType}</span>
            </Badge>
          </Link>
          <SourceBadge
            resourceKind={e.resourceKind ?? ""}
            resourceId={e.resourceId ?? ""}
            payload={e.payload}
          />
          <time
            dateTime={e.occurredAt}
            className="text-muted-foreground ml-auto shrink-0 font-mono text-xs"
          >
            {fmt.formatDateTime(e.occurredAt)}
          </time>
          <div className="w-full min-w-0">
            <PayloadPreview payload={e.payload} />
          </div>
        </div>
      )}
    />
  );
}
