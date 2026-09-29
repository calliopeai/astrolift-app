import { defaultListState } from "@/components/list/list-state";
import {
  CLUSTERS_LIST,
  clustersPageVariables,
} from "@/components/screens/clusters/list/clusters-list";
import { LIST_CLUSTERS_PAGE } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClustersClient } from "./clusters-client";

export const metadata = { title: "Clusters · Astrolift" };

/**
 * Preloads the first page of the fleet so the surface paints with rows
 * instead of skeletons.
 *
 * The variables have to be exactly the ones `useClustersList` sends on a
 * cold load or the preload is a cache miss, so they come from the same
 * pure function over the list's default state. A saved `?q=`, filter,
 * sort or page falls through to the client fetch.
 */
const FIRST_PAGE = clustersPageVariables(defaultListState(CLUSTERS_LIST));

export default function ClustersPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS_PAGE} variables={FIRST_PAGE}>
      <ClustersClient />
    </PreloadQuery>
  );
}
