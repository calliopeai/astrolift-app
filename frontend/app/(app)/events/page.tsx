import { EventsClient } from "./events-client";

export const metadata = { title: "Events · Astrolift" };

/**
 * No `PreloadQuery`: both feeds are cursor walks whose first request
 * carries a `limit` and a `search`, so a variable-less preload of the flat
 * list could only miss that cache entry and buy an extra SSR round trip.
 * The feed's skeleton covers the first paint instead.
 */
export default function EventsPage() {
  return <EventsClient />;
}
