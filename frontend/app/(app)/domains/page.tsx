import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DomainsClient } from "./domains-client";

export const metadata = { title: "Domains · Astrolift" };

export default function DomainsPage() {
  return (
    <PreloadQuery query={LIST_MANAGED_DOMAINS}>
      <DomainsClient />
    </PreloadQuery>
  );
}
