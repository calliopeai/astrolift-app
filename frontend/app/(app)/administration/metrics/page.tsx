import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { AdminMetricsClient } from "./metrics-client";

export const generateMetadata = routeTitle("metrics");

export default function AdministrationMetricsPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <AdminMetricsClient />
    </PreloadQuery>
  );
}
