import { routeTitle } from "@/lib/i18n/route-title";

import { AppAccessClient } from "./app-access-client";

export const generateMetadata = routeTitle("appAccess");

/** Admin › App access (#2534): who may enter every app, and whether the edge holds it. */
export default function AppAccessPage() {
  return <AppAccessClient />;
}
