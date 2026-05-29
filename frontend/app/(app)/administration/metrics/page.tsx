import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { MetricsClient } from "@/app/(app)/metrics/metrics-client";

export const metadata = { title: "Metrics · Astrolift" };

export default function AdministrationMetricsPage() {
  return (
    <PreloadQuery
      query={GET_DEPLOYMENT_METRICS}
      variables={{ windowDays: 30 }}
    >
      <PreloadQuery query={LIST_APP_HEALTH_SUMMARY}>
        <MetricsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
