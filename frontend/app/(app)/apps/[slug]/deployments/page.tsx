import {
  LIST_ENVIRONMENTS,
  LIST_PREVIEW_ENVIRONMENTS,
  LIST_PREVIEW_ENVIRONMENTS_PAGE,
} from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { activeSection, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";
import { APP_DEPLOYMENTS_PAGE } from "@/components/screens/apps/deployments/app-deployments-query";
import { PreloadQuery } from "@/lib/apollo";

import { AppPreviewsClient } from "../previews/previews-client";
import { AppDeploymentsClient } from "./deployments-client";

export const metadata = {
  title: "Deployments · Astrolift",
};

/**
 * The Deployments tab: the app's deployments on the embedded list, whose
 * views (All · Mine · Waiting approval · Failed · Today · Previews) are the
 * picker in its filter bar (spec 44 §4.4). Previews is a different row
 * shape, the app's preview environments, so `?view=previews` (where
 * `/previews` redirects) renders that screen; both share the one picker, so
 * the tab needs no section nav of its own.
 *
 * Each view preloads its first page so it paints with rows. The variables
 * have to be exactly the ones the hook sends on first render or the preload
 * is a cache miss: see `pageVariables` and `previewPageVariables` (default
 * page size 25, no search, no filter, newest start first, no cursor). A
 * link that already carries chips, a search or a cursor simply fetches on
 * the client.
 */
export default async function AppDeploymentsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const section = activeSection("deployments", await searchParams);
  return section === "previews" ? (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_PREVIEW_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <PreloadQuery
          query={LIST_PREVIEW_ENVIRONMENTS_PAGE}
          variables={{ appSlug: slug, search: null, limit: 25, after: null }}
        >
          <AppPreviewsClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  ) : (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
      <PreloadQuery
        query={APP_DEPLOYMENTS_PAGE}
        variables={{
          appSlug: slug,
          environmentName: null,
          statuses: null,
          search: null,
          filter: null,
          sort: "-started",
          limit: 25,
          after: null,
        }}
      >
        <AppDeploymentsClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
