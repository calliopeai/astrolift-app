import { PreloadQuery } from "@/lib/apollo";
import { LIST_ENVIRONMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";

import { EnvironmentsClient } from "./environments-client";

export const metadata = { title: "Environments · Astrolift" };

/**
 * Preloads the All view's first page. These have to be exactly the
 * variables `environmentsVariables` sends for it (no app, no search, no
 * filter, by app then name, 25 rows), or the preload is a cache miss.
 */
export default function EnvironmentsPage() {
  return (
    <PreloadQuery
      query={LIST_ENVIRONMENTS_PAGE}
      variables={{
        appSlug: null,
        search: null,
        filter: null,
        sort: "app,name",
        page: 1,
        pageSize: 25,
      }}
    >
      <EnvironmentsClient />
    </PreloadQuery>
  );
}
