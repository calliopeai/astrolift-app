import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClusterActivityClient } from "./cluster-activity-client";

export const metadata = { title: "Cluster activity · Astrolift" };

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
