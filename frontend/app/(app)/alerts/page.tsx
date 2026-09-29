import { LIST_ALERT_RULES_PAGE } from "@/graphql/operations/alerts.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AlertsClient } from "./alerts-client";

export const metadata = { title: "Alerts · Astrolift" };

/**
 * Preloads the rules list's first page so it paints with rows instead of
 * skeletons. The variables have to be exactly the ones `useAlerts` sends on
 * first paint (rulesVariables for the default list state) or the preload is
 * a cache miss. The events are their own route (/alerts/events) and are not
 * fetched here.
 */
const RULES_FIRST_PAGE = {
  target: null,
  targetId: null,
  activeOnly: false,
  search: null,
  limit: 25,
  after: null,
};

export default function AlertsPage() {
  return (
    <PreloadQuery query={LIST_ALERT_RULES_PAGE} variables={RULES_FIRST_PAGE}>
      <AlertsClient />
    </PreloadQuery>
  );
}
