import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppDomainsClient } from "./domains-client";

export const metadata = { title: "Domains · App · Astrolift" };

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
