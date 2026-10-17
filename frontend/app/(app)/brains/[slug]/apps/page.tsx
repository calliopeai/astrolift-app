import { routeTitle } from "@/lib/i18n/route-title";

import { AppsContent } from "../components/apps-content";

export const generateMetadata = routeTitle("apps", "semanticLayer");

/** Apps: the apps hosted on the layer, and Host an app. */
export default function BrainAppsPage() {
  return <AppsContent />;
}
