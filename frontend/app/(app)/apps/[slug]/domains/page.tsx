import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { AppDomainsClient } from "./domains-client";

export const generateMetadata = routeTitle("domains", "app");

export default async function AppDomainsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: slug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <AppDomainsClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
