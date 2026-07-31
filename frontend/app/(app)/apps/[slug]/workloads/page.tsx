import { GET_APP, LIST_WORKLOADS, LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WorkloadsListClient } from "./workloads-list-client";

export const metadata = {
  title: "Workloads · Astrolift",
};

/**
 * Preloads the table's first page so it paints with rows rather than
 * skeletons.
 *
 * The variables have to be exactly the ones `useCursorTable` sends on
 * first render or the preload is a cache miss: this app, no cursor, no
 * search, and DataTable's `DEFAULT_PAGE_SIZE`. (The constant is not
 * imported — it lives in a `"use client"` module, whose exports are
 * client references on the server.) A saved `?wl-q=` search or a changed
 * `?wl-size=` falls through to the client fetch.
 *
 * `LIST_WORKLOADS` stays preloaded alongside it: it is what the stats
 * strip reads, which the paginated field cannot answer.
 */
export default async function AppWorkloadsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
        <PreloadQuery
          query={LIST_WORKLOADS_PAGE}
          variables={{ appSlug: slug, search: null, limit: 25 }}
        >
          <WorkloadsListClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
