import { AlertEventsClient } from "./alert-events-client";

export const metadata = { title: "Alert events · Astrolift" };

/** Admin › Alerts › Events: the firing instances as a feed, All or Firing (`?view=`). */
export default function AlertEventsPage() {
  return <AlertEventsClient />;
}
