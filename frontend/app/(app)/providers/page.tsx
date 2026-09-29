import { LIST_CLUSTERS, LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProvidersClient } from "./providers-client";

export const metadata = { title: "Providers · Astrolift" };

/**
 * Unified Providers surface (#887 / #889 / #890). Preloads only the Cloud
 * tab's queries, the tab shown on arrival: the Source and Identity tabs
 * fetch when they are opened (list rule 2, hidden sections do not preload).
 * The tab is in the URL hash, which the server never sees.
 */
export default function ProvidersPage() {
  return (
    <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
      <PreloadQuery query={LIST_CLUSTERS}>
        <ProvidersClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
