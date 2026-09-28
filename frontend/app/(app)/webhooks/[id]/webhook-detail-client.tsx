"use client";

import { useWebhookDetail } from "@/components/screens/webhooks/use-webhook-detail";
import { WebhookDetailScreen } from "@/components/screens/webhooks/WebhookDetailScreen";

/**
 * Webhook subscription detail (#1106). The data lives in
 * useWebhookDetail; the screen owns the markup.
 */
export function WebhookDetailClient({ id }: { id: string }) {
  return <WebhookDetailScreen {...useWebhookDetail(id)} />;
}
