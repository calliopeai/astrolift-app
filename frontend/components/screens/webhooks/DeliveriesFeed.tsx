"use client";

import { SendIcon } from "lucide-react";
import Link from "next/link";

import { Feed } from "@/components/feed/Feed";
import { Badge } from "@/components/ui/badge";
import type { AstroliftWebhookDelivery } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { SubscriptionDeliveriesData } from "./use-webhooks";

export type DeliveriesFeedProps = SubscriptionDeliveriesData & {
  /** Each attempt links to its detail when set. */
  hrefOf?: (d: AstroliftWebhookDelivery) => string;
  /** The empty line's hint: where a test event is sent from. */
  emptyHint: string;
  maxHeight?: string;
};

/**
 * A subscription's delivery log (list rule 5): deliveries keep arriving, so
 * they are a Feed, newest first, grouped by day, loading older attempts as
 * the reader nears the end of the frame. Pure; the data is
 * useSubscriptionDeliveries.
 */
export function DeliveriesFeed({ deliveries, hrefOf, emptyHint, maxHeight }: DeliveriesFeedProps) {
  const fmt = useFormatters();
  return (
    <Feed<AstroliftWebhookDelivery>
      label="Deliveries"
      {...deliveries}
      keyOf={(d) => d.id}
      groupBy={{ day: (d) => d.deliveredAt }}
      maxHeight={maxHeight}
      dense
      empty={{
        icon: <SendIcon className="size-5" />,
        title: "No deliveries yet",
        description: emptyHint,
      }}
      renderItem={(d) => {
        const line = (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-1">
            <Badge variant={d.success ? "secondary" : "outline"} className="text-2xs font-mono">
              {d.statusCode ?? "ERR"}
            </Badge>
            <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
              {d.eventType}
            </span>
            {d.isTest && (
              <Badge variant="outline" className="text-2xs">
                test
              </Badge>
            )}
            <span className="text-muted-foreground ml-auto flex shrink-0 items-center gap-3 font-mono text-xs">
              <span title="Attempt">#{d.retryAttempt}</span>
              <span>{d.latencyMs}ms</span>
              <time dateTime={d.deliveredAt}>{fmt.formatDateTime(d.deliveredAt)}</time>
            </span>
            {(d.error || d.responseBodyExcerpt) && (
              <p className="text-muted-foreground line-clamp-1 w-full min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                {d.error || d.responseBodyExcerpt}
              </p>
            )}
          </div>
        );
        return hrefOf ? (
          <Link
            href={hrefOf(d)}
            className="hover:bg-muted/50 focus-visible:ring-ring block rounded-sm py-0.5 focus-visible:ring-2 focus-visible:outline-none"
          >
            {line}
          </Link>
        ) : (
          line
        );
      }}
    />
  );
}
