import { PreloadQuery } from "@/lib/apollo";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { routeTitle } from "@/lib/i18n/route-title";

import { EventDetailClient } from "./event-detail-client";

export const generateMetadata = routeTitle("event");

/**
 * Event detail (#1106) — drill-in target for an /events row.
 * Reuses the unfiltered LIST_EVENTS window (no singular query exists).
 */
export default async function EventDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_EVENTS} variables={{ limit: 200, eventType: null }}>
      <EventDetailClient id={id} />
    </PreloadQuery>
  );
}
