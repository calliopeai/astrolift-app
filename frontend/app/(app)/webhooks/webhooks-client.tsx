"use client";

import type * as React from "react";

import { SubscriptionDetailView } from "@/components/screens/webhooks/SubscriptionDetail";
import { useSubscriptionDeliveries, useWebhooks } from "@/components/screens/webhooks/use-webhooks";
import { WebhooksScreen } from "@/components/screens/webhooks/WebhooksScreen";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";

/**
 * Webhooks, platform-wide or scoped to one app/agent (`appSlug`). The
 * screen owns the markup; the deliveries sheet's body gets a container
 * here so its delivery feed runs only while the sheet is open.
 */
export function WebhooksClient({
  appSlug,
  tabs,
}: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  return (
    <WebhooksScreen
      {...useWebhooks(appSlug)}
      appSlug={appSlug}
      tabs={tabs}
      renderDetail={(subscription) => <SubscriptionDetail subscription={subscription} />}
    />
  );
}

function SubscriptionDetail({ subscription }: { subscription: AstroliftWebhookSubscription }) {
  return (
    <SubscriptionDetailView
      {...useSubscriptionDeliveries(subscription.id)}
      subscription={subscription}
    />
  );
}
