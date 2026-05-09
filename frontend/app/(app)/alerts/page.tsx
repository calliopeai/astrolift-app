import {
  LIST_ALERT_EVENTS,
  LIST_ALERT_RULES,
} from "@/graphql/operations/alerts.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AlertsClient } from "./alerts-client";

export const metadata = { title: "Alerts · Astrolift" };

export default function AlertsPage() {
  return (
    <PreloadQuery query={LIST_ALERT_RULES} variables={{ activeOnly: true }}>
      <PreloadQuery
        query={LIST_ALERT_EVENTS}
        variables={{ unresolvedOnly: false, limit: 100 }}
      >
        <AlertsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
