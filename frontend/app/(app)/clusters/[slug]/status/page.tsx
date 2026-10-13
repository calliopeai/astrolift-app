import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { ClusterStatusClient } from "./cluster-status-client";

export const generateMetadata = routeTitle("clusterStatus");

export default async function ClusterStatusPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterStatusClient slug={slug} />
    </PreloadQuery>
  );
}
