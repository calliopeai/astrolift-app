import {
  LIST_SOURCE_CONNECTIONS,
  LIST_SSH_DEPLOY_KEYS,
} from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SourceProvidersClient } from "./source-providers-client";

export const metadata = {
  title: "Source providers · Settings · Astrolift",
};

export default function SourceProvidersPage() {
  return (
    <PreloadQuery query={LIST_SOURCE_CONNECTIONS}>
      <PreloadQuery query={LIST_SSH_DEPLOY_KEYS} variables={{ appSlug: null }}>
        <SourceProvidersClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
