import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AdminMetricsClient } from "./metrics-client";

export const metadata = { title: "Metrics · Astrolift" };

export default function AdministrationMetricsPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <AdminMetricsClient />
    </PreloadQuery>
  );
}
