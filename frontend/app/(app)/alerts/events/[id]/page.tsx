import { PreloadQuery } from "@/lib/apollo";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";

import { AlertEventDetailClient } from "./alert-event-detail-client";

export const metadata = { title: "Alert event · Astrolift" };

/**
 * Alert event detail (#1106) — drill-in target for an /alerts event row.
 * Reuses the global LIST_ALERT_EVENTS window (no singular query exists).
 */
export default async function AlertEventDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_ALERT_EVENTS} variables={{ unresolvedOnly: false, limit: 100 }}>
      <AlertEventDetailClient id={id} />
    </PreloadQuery>
  );
}
