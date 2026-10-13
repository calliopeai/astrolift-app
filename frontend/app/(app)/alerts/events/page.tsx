import { routeTitle } from "@/lib/i18n/route-title";

import { AlertEventsClient } from "./alert-events-client";

export const generateMetadata = routeTitle("alertEvents");

/** Admin › Alerts › Events: the firing instances as a feed, All or Firing (`?view=`). */
export default function AlertEventsPage() {
  return <AlertEventsClient />;
}
