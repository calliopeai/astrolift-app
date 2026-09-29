"use client";

import { DefinitionList } from "@/components/ui/definition-list";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { DeliveriesFeed } from "./DeliveriesFeed";
import type { SubscriptionDeliveriesData } from "./use-webhooks";

export type SubscriptionDetailViewProps = SubscriptionDeliveriesData & {
  subscription: AstroliftWebhookSubscription;
};

/**
 * One subscription at a glance, the body of the sheet the webhooks list
 * opens from a row's `⋯`: its delivery state, then its delivery log as a
 * Feed in its own frame (list rules 3 and 5), so the list page never grows
 * a second table under the first.
 */
export function SubscriptionDetailView({ subscription, deliveries }: SubscriptionDetailViewProps) {
  const fmt = useFormatters();

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p className="font-mono text-xs [overflow-wrap:anywhere]">{subscription.url}</p>

      <DefinitionList
        orientation="stack"
        className="grid grid-cols-2 gap-3"
        items={[
          {
            term: "Secret rotated",
            description: subscription.secretRotatedAt
              ? fmt.formatDateTime(subscription.secretRotatedAt)
              : "never",
          },
          {
            term: "Last delivery",
            description: subscription.lastDeliveryAt
              ? fmt.formatDateTime(subscription.lastDeliveryAt)
              : "never",
          },
          {
            term: "Last status",
            description: (
              <span className="font-mono">{subscription.lastResponseStatus ?? "—"}</span>
            ),
          },
          {
            term: "Failure count",
            description: <span className="font-mono">{subscription.failureCount}</span>,
          },
        ]}
      />

      <div className="min-w-0 space-y-2">
        <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          Deliveries
        </p>
        <DeliveriesFeed
          deliveries={deliveries}
          emptyHint="Use the Send test event action to fire one."
          maxHeight="max-h-128"
        />
      </div>
    </div>
  );
}
