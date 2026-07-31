import { LIST_DEPLOYMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DeploymentsClient } from "./deployments-client";

export const metadata = { title: "Deployments · Astrolift" };

/**
 * Preloads the first page of the default view so the table paints with
 * rows instead of skeletons.
 *
 * These variables have to be exactly the ones `useCursorTable` sends for
 * that view or the preload is a cache miss: the Active tab's filter, no
 * cursor, no search, and DataTable's `DEFAULT_PAGE_SIZE`. (The constant
 * is not imported — it lives in a `"use client"` module, whose exports
 * are client references on the server.) A deep link into another tab, a
 * saved `?dep-q=` search or a changed page size simply falls through to
 * the client fetch.
 */
const ACTIVE_TAB_FIRST_PAGE = {
  statuses: ["pending", "deploying", "redeploying", "running"],
  isPreview: false,
  search: null,
  limit: 25,
};

export default function DeploymentsPage() {
  return (
    <PreloadQuery query={LIST_DEPLOYMENTS_PAGE} variables={ACTIVE_TAB_FIRST_PAGE}>
      <DeploymentsClient />
    </PreloadQuery>
  );
}
