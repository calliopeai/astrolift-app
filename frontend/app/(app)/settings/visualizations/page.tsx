import { routeTitle } from "@/lib/i18n/route-title";

import { VisualizationsClient } from "./visualizations-client";

export const generateMetadata = routeTitle("visualizations", "settings");

export default function VisualizationsPage() {
  return <VisualizationsClient />;
}
