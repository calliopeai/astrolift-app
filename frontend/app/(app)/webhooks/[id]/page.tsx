import { PreloadQuery } from "@/lib/apollo";
import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";

import { WebhookDetailClient } from "./webhook-detail-client";

export const metadata = { title: "Webhook · Astrolift" };

/**
 * Webhook subscription detail (#1106) — drill-in target for a /webhooks row.
 * Reuses the global LIST_WEBHOOKS window (no singular query exists).
 */
export default async function WebhookDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_WEBHOOKS} variables={{ appSlug: null }}>
      <WebhookDetailClient id={id} />
    </PreloadQuery>
  );
}
