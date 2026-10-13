import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { ClusterDetailClient } from "./cluster-detail-client";

export const generateMetadata = routeTitle("cluster");

export default async function ClusterDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterDetailClient slug={slug} />
    </PreloadQuery>
  );
}
