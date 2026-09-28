"use client";

import type * as React from "react";

import { SubscriptionDetailView } from "@/components/screens/webhooks/SubscriptionDetail";
import { useSubscriptionDeliveries, useWebhooks } from "@/components/screens/webhooks/use-webhooks";
import { WebhooksScreen } from "@/components/screens/webhooks/WebhooksScreen";
import type { AstroliftWebhookSubscription } from "@/graphql/operations/operations.types";

/**
 * Webhooks, platform-wide or scoped to one app/agent (`appSlug`). The
 * screen owns the markup; the expanded subscription's panel gets a
 * container here so its deliveries walk runs only while it is shown.
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
      renderDetail={(subscription, onClose) => (
        <SubscriptionDetail subscription={subscription} onClose={onClose} />
      )}
    />
  );
}

function SubscriptionDetail({
  subscription,
  onClose,
}: {
  subscription: AstroliftWebhookSubscription;
  onClose: () => void;
}) {
  return (
    <SubscriptionDetailView
      {...useSubscriptionDeliveries(subscription.id)}
      subscription={subscription}
      onClose={onClose}
    />
  );
}
