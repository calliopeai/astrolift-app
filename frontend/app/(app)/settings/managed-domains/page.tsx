import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/managed-domains.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ManagedDomainsClient } from "./managed-domains-client";

export const metadata = {
  title: "Managed domains · Settings · Astrolift",
};

export default function ManagedDomainsPage() {
  return (
    <PreloadQuery query={LIST_MANAGED_DOMAINS}>
      <ManagedDomainsClient />
    </PreloadQuery>
  );
}
