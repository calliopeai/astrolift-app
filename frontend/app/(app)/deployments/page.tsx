import { LIST_DEPLOYMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DeploymentsClient } from "./deployments-client";

export const metadata = { title: "Deployments · Astrolift" };

/**
 * Preloads the first page of the All view so the table paints with rows
 * instead of skeletons.
 *
 * These variables have to be exactly the ones `deploymentsVariables` sends
 * for that view or the preload is a cache miss: no filters, no search, no
 * cursor and the list's first page size. (It is not imported: the list
 * declaration calls a `"use client"` helper at module scope.) A deep link
 * into another view, a filter or a changed page size falls through to the
 * client fetch.
 */
const ALL_FIRST_PAGE = {
  appSlug: null,
  environmentName: null,
  statuses: null,
  search: null,
  limit: 25,
  after: null,
};

export default function DeploymentsPage() {
  return (
    <PreloadQuery query={LIST_DEPLOYMENTS_PAGE} variables={ALL_FIRST_PAGE}>
      <DeploymentsClient />
    </PreloadQuery>
  );
}
