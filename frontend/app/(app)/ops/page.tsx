import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { GET_DEPLOYMENT_METRICS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";
import {
  LIST_AUDIT_EVENTS,
  LIST_WORKFLOW_RUNS,
} from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { OpsClient } from "./ops-client";

export const metadata = {
  title: "Operations · Astrolift",
};

export default function OpsPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <PreloadQuery query={GET_DEPLOYMENT_METRICS} variables={{ windowDays: 1 }}>
        <PreloadQuery
          query={LIST_ALERT_EVENTS}
          variables={{ unresolvedOnly: true, limit: 10 }}
        >
          <PreloadQuery query={LIST_AUDIT_EVENTS} variables={{ limit: 10 }}>
            <PreloadQuery query={LIST_WORKFLOW_RUNS} variables={{ limit: 5 }}>
              <OpsClient />
            </PreloadQuery>
          </PreloadQuery>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
