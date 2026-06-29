import { LIST_CLUSTERS, LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS, LIST_SSH_DEPLOY_KEYS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProvidersClient } from "./providers-client";

export const metadata = { title: "Providers · Astrolift" };

/**
 * Unified Providers surface (#887 / #889 / #890). Preloads the queries
 * for all three tabs (cloud plugins + clusters, SCM connections + deploy
 * keys, identity providers) so each renders without a client-side fetch
 * waterfall when it first becomes visible.
 */
export default function ProvidersPage() {
  return (
    <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
      <PreloadQuery query={LIST_CLUSTERS}>
        <PreloadQuery query={LIST_SOURCE_CONNECTIONS}>
          <PreloadQuery query={LIST_SSH_DEPLOY_KEYS} variables={{ appSlug: null }}>
            <PreloadQuery query={LIST_IDENTITY_PROVIDERS}>
              <ProvidersClient />
            </PreloadQuery>
          </PreloadQuery>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
