import { LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProvidersClient } from "./providers-client";

export const metadata = { title: "Provider plugins · Astrolift" };

export default function ProvidersPage() {
  return (
    <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
      <ProvidersClient />
    </PreloadQuery>
  );
}
