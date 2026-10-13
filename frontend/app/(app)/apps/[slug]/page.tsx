import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { AppDetailClient } from "./app-detail-client";

export const generateMetadata = routeTitle("app");

export default async function AppDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug, includeDrift: true }}>
      <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
        <AppDetailClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
