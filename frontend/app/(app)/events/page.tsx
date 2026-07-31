import { EventsClient } from "./events-client";

export const metadata = { title: "Events · Astrolift" };

/**
 * No `PreloadQuery`: every list on this page is a cursor walk now, and the
 * controller's first request carries a `limit`, a `search` and (when the URL
 * restores one) a term — a variable-less preload of the deprecated flat list
 * could only miss that cache entry and buy an extra SSR round trip.
 * DataTable's skeleton covers the first paint instead.
 */
export default function EventsPage() {
  return <EventsClient />;
}
