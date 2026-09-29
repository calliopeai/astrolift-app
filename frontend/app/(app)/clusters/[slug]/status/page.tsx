import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClusterStatusClient } from "./cluster-status-client";

export const metadata = { title: "Cluster status · Astrolift" };

export default async function ClusterStatusPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_CLUSTER} variables={{ slug }}>
      <ClusterStatusClient slug={slug} />
    </PreloadQuery>
  );
}
