import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { ClusterActivityClient } from "./cluster-activity-client";

export const generateMetadata = routeTitle("clusterActivity");

export default async function ClusterActivityPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterActivityClient slug={slug} />
    </PreloadQuery>
  );
}
