import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { NewBrainClient } from "./new-brain-client";

export const generateMetadata = routeTitle("newSemanticLayer", "semanticLayers");

/**
 * Create a semantic layer (#2427): the same request as `astro brain create`,
 * then its progress at `?creation=<id>` until the first build is live. The
 * connections and projects the form starts from are read with the page.
 */
export default function NewBrainPage() {
  return (
    <PreloadQuery query={LIST_SOURCE_CONNECTIONS}>
      <PreloadQuery query={LIST_PROJECTS}>
        <NewBrainClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
