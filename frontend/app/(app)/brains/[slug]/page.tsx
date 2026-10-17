import { routeTitle } from "@/lib/i18n/route-title";

import { OverviewContent } from "./components/overview-content";

export const generateMetadata = routeTitle("overview", "semanticLayer");

/** Overview, at the layer's root: health, revision, freshness and endpoints. */
export default function BrainOverviewPage() {
  return <OverviewContent />;
}
