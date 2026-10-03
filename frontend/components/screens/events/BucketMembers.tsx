"use client";

import NextLink from "next/link";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import { eventPayloadText } from "./event-source";
import type { useBucketMembers } from "./use-events";

export type BucketMembersProps = ReturnType<typeof useBucketMembers>;

/** The raw events folded into one bucket, each linked to its detail. */
export function BucketMembers({ loading, members }: BucketMembersProps) {
  const fmt = useFormatters();

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
            <Badge variant="outline" className="max-w-full shrink font-mono text-xs">
              <span className="min-w-0 truncate" title={e.eventType}>
                {e.eventType}
              </span>
            </Badge>
            <span className="text-muted-foreground text-xs">
              {fmt.formatDateTime(e.occurredAt)}
            </span>
          </NextLink>
          {eventPayloadText(e.payload) !== null && (
            <pre className="bg-muted text-muted-foreground mt-2 overflow-x-auto rounded-md p-2 text-xs">
              {JSON.stringify(e.payload, null, 2)}
            </pre>
          )}
        </li>
      ))}
    </ul>
  );
}
