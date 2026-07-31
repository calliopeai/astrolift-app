import {
  LIST_PREVIEW_ENVIRONMENTS,
  LIST_PREVIEW_ENVIRONMENTS_PAGE,
} from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppPreviewsClient } from "./previews-client";

export const metadata = {
  title: "Previews · Astrolift",
};

/**
 * Preloads the table's first page so it paints with rows rather than
 * skeletons.
 *
 * The variables have to be exactly the ones `useCursorTable` sends on
 * first render or the preload is a cache miss: this app, no cursor, no
 * search, and DataTable's `DEFAULT_PAGE_SIZE`. (The constant is not
 * imported — it lives in a `"use client"` module, whose exports are
 * client references on the server.) A saved `?pv-q=` search or a changed
 * `?pv-size=` falls through to the client fetch.
 *
 * `LIST_PREVIEW_ENVIRONMENTS` stays preloaded alongside it: it is what
 * the spend roll-up and the stale sweep read, which the paginated field
 * cannot answer.
 */
export default async function AppPreviewsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_PREVIEW_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <PreloadQuery
          query={LIST_PREVIEW_ENVIRONMENTS_PAGE}
          variables={{ appSlug: slug, search: null, limit: 25 }}
        >
          <AppPreviewsClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
