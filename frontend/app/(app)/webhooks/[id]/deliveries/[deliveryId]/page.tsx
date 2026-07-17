import { PreloadQuery } from "@/lib/apollo";
import { LIST_WEBHOOK_DELIVERIES } from "@/graphql/operations/operations.queries";

import { DeliveryDetailClient } from "./delivery-detail-client";

export const metadata = { title: "Webhook delivery · Astrolift" };

/**
 * Webhook delivery detail (#1106) — drill-in target for a delivery row on the
 * webhook detail. Reuses LIST_WEBHOOK_DELIVERIES for the subscription (no
 * singular delivery query exists).
 */
export default async function WebhookDeliveryDetailPage({
  params,
}: {
  params: Promise<{ id: string; deliveryId: string }>;
}) {
  const { id, deliveryId } = await params;
  return (
    <PreloadQuery query={LIST_WEBHOOK_DELIVERIES} variables={{ subscriptionId: id, limit: 50 }}>
      <DeliveryDetailClient subscriptionId={id} deliveryId={deliveryId} />
    </PreloadQuery>
  );
}
