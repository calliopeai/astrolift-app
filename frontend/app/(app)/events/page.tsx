import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { EventsClient } from "./events-client";

export const metadata = { title: "Events · Astrolift" };

export default function EventsPage() {
  return (
    <PreloadQuery
      query={LIST_EVENTS}
      variables={{ limit: 200, eventType: null }}
    >
      <EventsClient />
    </PreloadQuery>
  );
}
