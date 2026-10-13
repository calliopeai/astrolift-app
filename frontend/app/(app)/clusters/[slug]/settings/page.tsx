import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { ClusterSettingsClient } from "./cluster-settings-client";

export const generateMetadata = routeTitle("clusterSettings");

export default async function ClusterSettingsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterSettingsClient slug={slug} />
    </PreloadQuery>
  );
}
