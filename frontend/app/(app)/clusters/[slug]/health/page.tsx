import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { ClusterHealthClient } from "./cluster-health-client";

export const generateMetadata = routeTitle("clusterHealth");

export default async function ClusterHealthPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterHealthClient slug={slug} />
    </PreloadQuery>
  );
}
