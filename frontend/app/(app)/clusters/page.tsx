import { LIST_CLUSTERS_PAGE } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClustersClient } from "./clusters-client";

export const metadata = { title: "Clusters · Astrolift" };

/**
 * Preloads the first page of the fleet so the surface paints with rows
 * instead of skeletons.
 *
 * The variables have to be exactly the ones `useCursorTable` sends on a
 * cold load or the preload is a cache miss: no cursor, no search, and
 * DataTable's `DEFAULT_PAGE_SIZE`. (The constant is not imported — it
 * lives in a `"use client"` module, whose exports are client references
 * on the server.) A saved `?cluster-q=` search or a changed page size
 * falls through to the client fetch.
 */
const FIRST_PAGE = { search: null, limit: 25 };

export default function ClustersPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS_PAGE} variables={FIRST_PAGE}>
      <ClustersClient />
    </PreloadQuery>
  );
}
