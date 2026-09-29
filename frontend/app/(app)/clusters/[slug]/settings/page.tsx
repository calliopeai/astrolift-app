import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClusterSettingsClient } from "./cluster-settings-client";

export const metadata = { title: "Cluster settings · Astrolift" };

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
