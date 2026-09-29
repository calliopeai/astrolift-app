"use client";

import { DeliveryDetailScreen } from "@/components/screens/webhooks/DeliveryDetailScreen";
import { useWebhookDelivery } from "@/components/screens/webhooks/use-webhook-detail";

/**
 * Webhook delivery detail (#1106). The data lives in useWebhookDelivery;
 * the screen owns the markup.
 */
export function DeliveryDetailClient({
  subscriptionId,
  deliveryId,
}: {
  subscriptionId: string;
  deliveryId: string;
}) {
  return <DeliveryDetailScreen {...useWebhookDelivery(subscriptionId, deliveryId)} />;
}
