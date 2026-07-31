import { LIST_ALERT_EVENTS_PAGE, LIST_ALERT_RULES_PAGE } from "@/graphql/operations/alerts.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AlertsClient } from "./alerts-client";

export const metadata = { title: "Alerts · Astrolift" };

/**
 * Preloads the first page of each table so they paint with rows instead
 * of skeletons.
 *
 * The variables have to be exactly the ones `useCursorTable` sends on
 * first paint or the preload is a cache miss: the table's static filter,
 * no cursor, no search, and DataTable's `DEFAULT_PAGE_SIZE`. (The
 * constant is not imported — it lives in a `"use client"` module, whose
 * exports are client references on the server.) A saved `?rule-q=` search
 * or a changed page size falls through to the client fetch.
 */
const RULES_FIRST_PAGE = { activeOnly: false, search: null, limit: 25 };
const EVENTS_FIRST_PAGE = { unresolvedOnly: false, search: null, limit: 25 };

export default function AlertsPage() {
  return (
    <PreloadQuery query={LIST_ALERT_RULES_PAGE} variables={RULES_FIRST_PAGE}>
      <PreloadQuery query={LIST_ALERT_EVENTS_PAGE} variables={EVENTS_FIRST_PAGE}>
        <AlertsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
