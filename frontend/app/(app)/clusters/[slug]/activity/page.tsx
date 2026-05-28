import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
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
    <PreloadQuery query={LIST_CLUSTERS}>
      <ClusterActivityClient slug={slug} />
    </PreloadQuery>
  );
}
